import subprocess
import io
import os.path
from urllib import request
import tempfile
from pkg_resources import resource_filename
from .data import LispWrapper, Cons, Symbol, Quote
from .reader import Readtable
from .writer import lispify

_DEFAULT_COMMAND = ('sbcl', '--script')


class Lisp:
    debug: bool
    _backtrace: bool

    def __init__(self, cmd=_DEFAULT_COMMAND, quicklisp=False, debug=False,
                 backtrace=True):
        command = list(cmd)
        p = subprocess.Popen(command + [resource_filename(__name__, 'py.lisp')],
                             stdin = subprocess.PIPE,
                             stdout = subprocess.PIPE,
                             stderr = subprocess.PIPE,
                             shell = False)
        self.process = p
        self.stdin = io.TextIOWrapper(p.stdin, write_through=True,
                                      line_buffering=1,
                                      encoding='utf-8')
        self.stdout = io.TextIOWrapper(p.stdout, encoding='utf-8')
        # The name of the current package.
        self.package = "COMMON-LISP-USER"
        # Each Lisp process has its own readtable.
        self.readtable = Readtable(self)
        # The classes dict maps from symbols to python classes.
        self.classes = {}
        # Whenever the reader encounters a Lisp object whose class is not
        # yet known, it stores it in this {class_name : instances} dict.
        # This allows us to patch these instances later.
        self.unpatched_instances = {}
        # If debug is true, cl4py will print plenty of debug information.
        self.debug = debug

        # Collect ASDF -- we'll need it for UIOP later
        self.function('CL:REQUIRE')(Symbol("ASDF", "KEYWORD"))

        # Finally, check whether the user wants quicklisp to be available.
        self.quicklisp = quicklisp
        if quicklisp:
            install_and_load_quicklisp(self)
        self._backtrace = backtrace
        self.eval( ('defparameter', 'cl4py::*backtrace*', backtrace) )



    @property
    def backtrace(self) -> bool:
        return self._backtrace


    @backtrace.setter
    def backtrace(self, value: bool) -> bool:
        self.eval ( ('setf', 'cl4py::*backtrace*', Quote(value)))
        self._backtrace = value
        return self._backtrace


    def __del__(self):
        alive = self.process.poll() == None
        if alive:
            self.stdin.write('(cl4py:quit)\n')
            self.process.wait()


    def eval(self, expr, return_lisp_output=False, print_lisp_output=False):
        """
        Evaluate expression

        Parameters
        ----------
        expr
            Expression
        return_lisp_output
            Whether to return lisp's output message. If False, cl4py's original
            behavior takes place. Otherwise, a tuple is returned, with the original
            return value as first value, and lisp's output message as second one.
        print_lisp_output
            Whether to print lisp's output message

        Returns
        -------
        Union[None, Cons, tuple]
            result object, or: (result object, message)
        """
        sexp = lispify(self, expr)
        if self.debug: print(sexp) # pylint: disable=multiple-statements
        self.stdin.write(sexp + '\n')
        pkg = self.readtable.read(self.stdout)
        val = self.readtable.read(self.stdout)
        err = self.readtable.read(self.stdout)
        output = self.readtable.read(self.stdout)
        # Update the current package.
        self.package = pkg
        if print_lisp_output:
            # Write the Lisp output to the Python output.
            print(output, end='')
        # If there is an error, raise it.
        if isinstance(err, Cons):
            condition = err.car
            msg = err.cdr.car if err.cdr else ""
            def init(self):
                RuntimeError.__init__(self, msg)
            raise type(str(condition), (RuntimeError,),
                       {'__init__': init})()
        # Now, check whether there are any unpatched instances.  If so,
        # figure out their class definitions and patch them accordingly.
        items = list(self.unpatched_instances.items())
        self.unpatched_instances.clear()
        for (cls_name, instances) in items:
            cls = type(cls_name.python_name, (LispWrapper,), {})
            self.classes[cls_name] = cls
            alist = self.function('cl4py:class-information')(cls_name)
            for cons in alist:
                add_member_function(cls, cons.car, cons.cdr)
            for instance in instances:
                instance.__class__ = cls

        # Finally, return the resulting values.
        if return_lisp_output:
            if val == ():
                return None, output
            elif val.cdr == ():
                return val.car, output
            else:
                return tuple(val), output
        else:
            if val == ():
                return None
            elif val.cdr == ():
                return val.car
            else:
                return tuple(val)


    def find_package(self, name):
        return self.function('CL:FIND-PACKAGE')(name)


    def function(self, name):
        return self.eval( ('CL:FUNCTION', name) )


def add_member_function(cls, name, gf):
    method_name = name.python_name
    setattr(cls, method_name, lambda self, *args: gf(self, *args))


def install_and_load_quicklisp(lisp):
    quicklisp_setup = os.path.expanduser('~/quicklisp/setup.lisp')
    if os.path.isfile(quicklisp_setup):
        lisp.function('cl:load')(quicklisp_setup)
    else:
        install_quicklisp(lisp)


def install_quicklisp(lisp):
    url = 'https://beta.quicklisp.org/quicklisp.lisp'
    with tempfile.NamedTemporaryFile(prefix='quicklisp-', suffix='.lisp') as tmp:
        with request.urlopen(url) as u:
            tmp.write(u.read())
        lisp.function('cl:load')(tmp.name)
    print('Installing Quicklisp...')
    lisp.eval( ('quicklisp-quickstart:install',) )
