# Author-Peter Ludikar, Gary Singer
# Description-An Add-In for making dog-bone fillets, as a single editable Custom Feature in the Timeline.
import os
import sys

_appPath = os.path.dirname(os.path.abspath(__file__))
_subpath = os.path.join(_appPath, 'py_packages')

if _subpath not in sys.path:
	sys.path.insert(0, _subpath)

from .fusion_dogbone import DogboneCFAddIn
from .fusion_util import log, handleException


log(f'Loading Fusion Add-In {__file__!r} ...')


thisAddIn: DogboneCFAddIn = None


def run(context):
	try:
		log(f'Starting Fusion Add-In {__file__!r} ...')
		global thisAddIn
		thisAddIn = DogboneCFAddIn()
	except:
		handleException()


def stop(context):
	try:
		log(f'Stopping Fusion Add-In {__file__!r} ...')
		global thisAddIn
		del thisAddIn
	except:
		handleException()


log(f'Finished loading Fusion Add-In {__file__!r}')
