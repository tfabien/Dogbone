import adsk.core
import adsk.fusion
from dataclasses import dataclass, field
import re
from typing import Union

from .fusion_cf_addin import FusionCustomFeatureAddIn
from .fusion_util import *
from .dogbone_base_combines import DogboneBaseCombines
from .lib.classes import DbParams, DbFace, Selection
from .lib.utils import getTopFace
from .constants import (
	NORMAL_DOGBONE, MINIMAL_DOGBONE, MORTISE_DOGBONE,
	FROM_SELECTED_FACE, FROM_TOP_FACE, ON_LONG_SIDE, ON_SHORT_SIDE)

try:
	log('fusion_dogbone.py module loaded')
except:
	# Application/UI may not be ready yet at import time.
	pass


class _NullSelectionInput:
	"""
	Stand-in for the `commandInputsEdgeSelect` UI control that `DbFace.registerEdges`
	populates as it auto-detects corner edges. There is no such UI here (edges are
	always fully re-derived from the selected faces + angle parameters, never
	individually overridden by the user), so this just swallows the calls.
	"""
	def addSelection(self, entity):
		pass


@dataclass
class DogboneParameters:
	"""
	All parameters that define a specific set of dogbones.

	Note that assignment of default values is deferred to instance creation time
	(using `default_factory`) rather than using static values because `Parameter` units
	will vary within the context of the current document and user preferences.
	"""
	faces: list[EntityRef] = field(default_factory=list)
	toolDia: Parameter = field(
		default_factory=lambda: Parameter.length(centimeters=cmOrIn(0.635, 0.25)))  # 0.25in
	toolDiaOffset: Parameter = field(
		default_factory=lambda: Parameter.length(centimeters=0))
	dbType: str = NORMAL_DOGBONE
	fromTop: bool = True
	mortiseType: bool = False
	longSide: bool = True
	minimalPercent: Parameter = field(default_factory=lambda: Parameter(10.0))
	acuteAngle: bool = False
	obtuseAngle: bool = False
	minAngleLimit: Parameter = field(default_factory=lambda: Parameter(89.0))
	maxAngleLimit: Parameter = field(default_factory=lambda: Parameter(91.0))


class DogboneCFAddIn(FusionCustomFeatureAddIn):
	"""
	The Dogbone Fusion Add-In, as a single editable Custom Feature in the Timeline.
	"""
	def __init__(self):
		super().__init__(
			baseCommandId='PJLDB_DogboneCF',
			name='Dogbone',
			createTooltip='Create dogbones at inside corners dropping from selected faces.',
			editTooltip='Edit dogbones.',
			resourceFolder='Resources/DogboneCF',
			toolbarControls=[
				{
					'workspace': 'FusionSolidEnvironment',
					'panel': 'SolidModifyPanel',
					'afterControl': 'FusionCombineCommand',
					'promote': True,
				},
			],
		)

	def createInputs(self, command: adsk.core.Command, params: DogboneParameters):
		inputs = command.commandInputs

		facesInput = inputs.addSelectionInput(
			'faces',
			'Face',
			'Select faces to drop dogbones from all inside corner edges.')
		facesInput.addSelectionFilter('PlanarFaces')
		facesInput.tooltip = 'Select faces to drop dogbones from all inside corner edges.'
		facesInput.setSelectionLimits(1)

		input = inputs.addValueInput(
			'toolDia', 'Tool Dia',
			unitType=params.toolDia.units,
			initialValue=params.toolDia.valueInput)
		input.minimumValue = 0
		input.isMinimumInclusive = False
		input.isMinimumLimited = True
		input.tooltip = "Diameter of the tool with which you'll cut the dogbone."

		input = inputs.addValueInput(
			'toolDiaOffset', 'Tool diameter offset',
			unitType=params.toolDiaOffset.units,
			initialValue=params.toolDiaOffset.valueInput)
		input.tooltip = 'Increases the tool diameter'
		input.tooltipDescription = (
			'Use this to create an oversized dogbone.<br>'
			'Normally set to 0.<br>'
			'A value of .010 would increase the dogbone diameter by .010<br>'
			'Used when you want to keep the tool diameter and oversize value separate')

		modeGroup = inputs.addGroupCommandInput('modeGroup', 'Mode')
		modeGroup.isExpanded = True
		modeInputs = modeGroup.children

		typeRow = modeInputs.addButtonRowCommandInput('dbType', 'Type', False)
		typeRow.listItems.add(NORMAL_DOGBONE, params.dbType == NORMAL_DOGBONE, 'Resources/ui/type/normal')
		typeRow.listItems.add(MINIMAL_DOGBONE, params.dbType == MINIMAL_DOGBONE, 'Resources/ui/type/minimal')
		typeRow.listItems.add(MORTISE_DOGBONE, params.dbType == MORTISE_DOGBONE, 'Resources/ui/type/hidden')
		typeRow.tooltipDescription = (
			'Minimal dogbones creates visually less prominent dogbones, but results in an interference fit '
			'that, for example, will require a larger force to insert a tenon into a mortise.<br><br>'
			'Mortise dogbones create dogbones on the shortest sides, or the longest sides.<br>'
			"A piece with a tenon can be used to hide them if they're not cut all the way through the workpiece.")

		mortiseRow = modeInputs.addButtonRowCommandInput('longSide', 'Mortise Type', False)
		mortiseRow.listItems.add(ON_LONG_SIDE, params.longSide, 'Resources/ui/type/hidden/longside')
		mortiseRow.listItems.add(ON_SHORT_SIDE, not params.longSide, 'Resources/ui/type/hidden/shortside')
		mortiseRow.tooltipDescription = (
			'Along Longest will have the dogbones cut into the longer sides.<br>'
			'Along Shortest will have the dogbones cut into the shorter sides.')
		mortiseRow.isVisible = params.dbType == MORTISE_DOGBONE

		input = modeInputs.addFloatSpinnerCommandInput(
			'minimalPercent', 'Percentage Reduction',
			unitType=Parameter.UNITLESS, min=0.1, max=100, spinStep=1,
			initialValue=clamp(params.minimalPercent.value, 0.1, 100))
		input.expression = params.minimalPercent.expression
		input.tooltip = 'Percentage of tool radius added to push out dogBone - leaves actual corner exposed'
		input.tooltipDescription = 'This should typically be left at 10%, but if the fit is too tight, it should be reduced'
		input.isVisible = params.dbType == MINIMAL_DOGBONE

		depthRow = modeInputs.addButtonRowCommandInput('fromTop', 'Depth Extent', False)
		depthRow.listItems.add(FROM_SELECTED_FACE, not params.fromTop, 'Resources/ui/mode/fromFace')
		depthRow.listItems.add(FROM_TOP_FACE, params.fromTop, 'Resources/ui/mode/fromTop')
		depthRow.tooltipDescription = (
			'When "From Top Face" is selected, all dogbones will be extended to the top most face<br><br>'
			"This is typically chosen when you don't want to, or can't do, double sided machining")

		angleGroup = inputs.addGroupCommandInput('angleGroup', 'Detection Mode')
		angleGroup.isExpanded = params.acuteAngle or params.obtuseAngle
		angleInputs = angleGroup.children

		input = angleInputs.addBoolValueInput('acuteAngle', 'Acute Angle', True, '', params.acuteAngle)
		input.tooltip = 'Enables detection of corner angles less than 90'

		input = angleInputs.addFloatSliderCommandInput('minAngleLimit', 'Min Limit', '', 10.0, 89.0)
		input.valueOne = clamp(params.minAngleLimit.value, 10.0, 89.0)
		input.isVisible = params.acuteAngle

		input = angleInputs.addBoolValueInput('obtuseAngle', 'Obtuse Angle', True, '', params.obtuseAngle)
		input.tooltip = 'Enables detection of corner angles greater than 90'

		input = angleInputs.addFloatSliderCommandInput('maxAngleLimit', 'Max Limit', '', 91.0, 170.0)
		input.valueOne = clamp(params.maxAngleLimit.value, 91.0, 170.0)
		input.isVisible = params.obtuseAngle

		# For error message output.
		inputs.addTextBoxCommandInput('error', '', '', numRows=1, isReadOnly=True)

		self._updateVisibility(inputs)

	def _updateVisibility(self, commandInputs: adsk.core.CommandInputs):
		dbType = commandInputs.itemById('dbType').selectedItem.name
		commandInputs.itemById('minimalPercent').isVisible = dbType == MINIMAL_DOGBONE
		commandInputs.itemById('longSide').isVisible = dbType == MORTISE_DOGBONE

		commandInputs.itemById('minAngleLimit').isVisible = commandInputs.itemById('acuteAngle').value
		commandInputs.itemById('maxAngleLimit').isVisible = commandInputs.itemById('obtuseAngle').value

	def canSelect(self, entity, input: adsk.core.SelectionCommandInput) -> bool:
		if input.id == 'faces':
			if entity.geometry.surfaceType != adsk.core.SurfaceTypes.PlaneSurfaceType:
				return False
			if not entity.body.isSolid:
				return False
		return True

	def areInputsValid(self, commandInputs: adsk.core.CommandInputs) -> bool:
		self._updateVisibility(commandInputs)

		errorMessage = None
		try:
			for input in allInputs(commandInputs):
				if hasattr(input, 'isValidExpression') and not input.isValidExpression:
					raise UserInputError(input, 'is invalid')
		except UserInputError as error:
			errorMessage = f'<span style="color:red"><b>{error.input.name}</b> {error.message}.</span>'

		# Set error message only if it has changed.
		errorBox: adsk.core.TextBoxCommandInput = commandInputs.itemById('error')
		if errorMessage:
			if errorBox.text != withoutHtml(errorMessage):
				errorBox.formattedText = errorMessage
		elif errorBox.text:
			errorBox.formattedText = ''

		return errorMessage is None

	def defaultParams(self) -> DogboneParameters:
		return DogboneParameters()

	def paramsToInputs(self, params: DogboneParameters, commandInputs: adsk.core.CommandInputs):
		facesInput: adsk.core.SelectionCommandInput = commandInputs.itemById('faces')
		facesInput.clearSelection()
		for face in params.faces:
			facesInput.addSelection(face.entity)

		commandInputs.itemById('toolDia').expression = params.toolDia.expression
		commandInputs.itemById('toolDiaOffset').expression = params.toolDiaOffset.expression

		for item in commandInputs.itemById('dbType').listItems:
			item.isSelected = item.name == params.dbType

		for item in commandInputs.itemById('longSide').listItems:
			item.isSelected = (item.name == ON_LONG_SIDE) == params.longSide

		commandInputs.itemById('minimalPercent').expression = params.minimalPercent.expression

		for item in commandInputs.itemById('fromTop').listItems:
			item.isSelected = (item.name == FROM_TOP_FACE) == params.fromTop

		commandInputs.itemById('acuteAngle').value = params.acuteAngle
		commandInputs.itemById('minAngleLimit').valueOne = params.minAngleLimit.value
		commandInputs.itemById('obtuseAngle').value = params.obtuseAngle
		commandInputs.itemById('maxAngleLimit').valueOne = params.maxAngleLimit.value

		self._updateVisibility(commandInputs)

	def inputsToParams(self, commandInputs: adsk.core.CommandInputs) -> DogboneParameters:
		facesInput: adsk.core.SelectionCommandInput = commandInputs.itemById('faces')
		faces = [EntityRef(facesInput.selection(i).entity) for i in range(0, facesInput.selectionCount)]

		return DogboneParameters(
			faces=faces,
			toolDia=Parameter(commandInputs.itemById('toolDia')),
			toolDiaOffset=Parameter(commandInputs.itemById('toolDiaOffset')),
			dbType=commandInputs.itemById('dbType').selectedItem.name,
			fromTop=commandInputs.itemById('fromTop').selectedItem.name == FROM_TOP_FACE,
			mortiseType=False,
			longSide=commandInputs.itemById('longSide').selectedItem.name == ON_LONG_SIDE,
			minimalPercent=Parameter(commandInputs.itemById('minimalPercent')),
			acuteAngle=commandInputs.itemById('acuteAngle').value,
			obtuseAngle=commandInputs.itemById('obtuseAngle').value,
			# FloatSliderCommandInput.expressionOne throws (InternalValidationError:
			# pInputUnits) on a unitless slider - read .valueOne instead, which is
			# also all the original Dogbone dialog ever reads from these sliders.
			minAngleLimit=Parameter(commandInputs.itemById('minAngleLimit').valueOne),
			maxAngleLimit=Parameter(commandInputs.itemById('maxAngleLimit').valueOne),
		)

	def customFeatureToParams(
			self,
			feature: adsk.fusion.CustomFeature,
			parameters: dict[str, Union[adsk.fusion.CustomFeatureParameter, Parameter]],
	) -> DogboneParameters:
		customNamedValues = feature.customNamedValues

		faces = [EntityRef(token) for token in
			customNamedValues.value('faces').split()]

		return DogboneParameters(
			faces=faces,
			toolDia=Parameter(parameters.get('toolDia')),
			toolDiaOffset=Parameter(parameters.get('toolDiaOffset')),
			dbType=customNamedValues.value('dbType'),
			fromTop=customNamedValues.value('fromTop') == 'true',
			mortiseType=customNamedValues.value('mortiseType') == 'true',
			longSide=customNamedValues.value('longSide') == 'true',
			minimalPercent=Parameter(parameters.get('minimalPercent', 10.0)),
			acuteAngle=customNamedValues.value('acuteAngle') == 'true',
			obtuseAngle=customNamedValues.value('obtuseAngle') == 'true',
			minAngleLimit=Parameter(parameters.get('minAngleLimit', 89.0)),
			maxAngleLimit=Parameter(parameters.get('maxAngleLimit', 91.0)),
		)

	def getCustomParameters(self, params: DogboneParameters) -> dict[str, Parameter]:
		return {
			'toolDia': params.toolDia,
			'toolDiaOffset': params.toolDiaOffset,
			'minimalPercent': params.minimalPercent,
			'minAngleLimit': params.minAngleLimit,
			'maxAngleLimit': params.maxAngleLimit,
		}

	def getCustomParameterDescriptions(self) -> dict[str, str]:
		return {
			'toolDia': 'Tool Diameter',
			'toolDiaOffset': 'Tool Diameter Offset',
			'minimalPercent': 'Minimal Dogbone Percent',
			'minAngleLimit': 'Min Angle Limit',
			'maxAngleLimit': 'Max Angle Limit',
		}

	def getCustomNamedValues(self, params: DogboneParameters) -> dict[str, str]:
		return {
			'faces': ' '.join(face.entityToken for face in params.faces),
			'dbType': params.dbType,
			'fromTop': 'true' if params.fromTop else 'false',
			'mortiseType': 'true' if params.mortiseType else 'false',
			'longSide': 'true' if params.longSide else 'false',
			'acuteAngle': 'true' if params.acuteAngle else 'false',
			'obtuseAngle': 'true' if params.obtuseAngle else 'false',
		}

	def getDependencies(self, params: DogboneParameters) -> dict[str, adsk.core.Base]:
		# Depend on the selected faces themselves, not their parent bodies: BRepBody
		# dependencies are unreliable at triggering `customFeatureCompute` when an
		# upstream feature changes (a known Fusion API gap - see Autodesk Community
		# "Custom Feature API Preview - Feedback Thread"), whereas face/edge
		# dependencies do wake it up.
		faces = {}
		for faceRef in params.faces:
			entity = faceRef.entity
			if entity:
				faces[entity.entityToken] = entity
		return {f'face{i}': face for i, face in enumerate(faces.values())}

	def createOrUpdateChildFeatures(self,
		params: DogboneParameters,
		existingFeatures: list[adsk.fusion.Feature],
		allowFeatureCreationAndDeletion: bool
	) -> list[adsk.fusion.Feature]:
		baseCombines = computeDogbones(params)
		return baseCombines.createOrUpdate(
			existingFeatures=existingFeatures,
			allowFeatureCreationAndDeletion=allowFeatureCreationAndDeletion)


def _makeDbParams(params: DogboneParameters) -> DbParams:
	"""
	Builds a `DbParams` snapshot purely from this feature's own parameters.

	`DbParams.__post_init__` reads a shared `defaults.dat` preferences file (used by
	the classic Dogbone dialog to remember "last used settings") and overwrites the
	whole `__dict__` if that file is present. That's fine for a fresh interactive
	dialog, but wrong for a CustomFeature recompute: this feature's own stored
	parameter values must always win, never whatever another document last left in
	that shared file. Explicitly setting every field after construction sidesteps it.

	Lengths are re-expressed as bare-cm expressions (e.g. "1.5 cm") via
	`formatInternalValue`, since `DbParams.toolDia`/`toolDiaOffset` evaluate
	`toolDiaStr`/`toolDiaOffsetStr` with no explicit unit context and would otherwise
	misinterpret a unit-less expression using the document's default length unit.
	"""
	unitsManager = adsk.core.Application.get().activeProduct.unitsManager
	dbParams = DbParams()
	dbParams.toolDiaStr = unitsManager.formatInternalValue(params.toolDia.value, 'cm', True)
	dbParams.toolDiaOffsetStr = unitsManager.formatInternalValue(params.toolDiaOffset.value, 'cm', True)
	dbParams.dbType = params.dbType
	dbParams.fromTop = params.fromTop
	dbParams.mortiseType = params.mortiseType
	dbParams.longSide = params.longSide
	dbParams.minimalPercent = params.minimalPercent.value
	dbParams.acuteAngle = params.acuteAngle
	dbParams.obtuseAngle = params.obtuseAngle
	dbParams.minAngleLimit = params.minAngleLimit.value
	dbParams.maxAngleLimit = params.maxAngleLimit.value
	return dbParams


def computeDogbones(params: DogboneParameters) -> DogboneBaseCombines:
	baseCombines = DogboneBaseCombines()

	if not params.faces:
		return baseCombines

	# De-duplicate: a face selected/stored twice would register its corner edges twice,
	# unioning the same tool body into itself and wasting work (harmless geometrically,
	# but pointless) -- mirrors BoxJoint's own face de-duplication.
	seenTokens = set()
	uniqueFaceRefs = []
	for faceRef in params.faces:
		if faceRef.entityToken not in seenTokens:
			seenTokens.add(faceRef.entityToken)
			uniqueFaceRefs.append(faceRef)

	dbParams = _makeDbParams(params)
	selection = Selection()

	# Group the selected faces by their target body: edges (and so tool bodies) are
	# always confined to a single body, so grouping this way -- rather than by
	# occurrence, as the classic Dogbone add-in does -- gives each target body exactly
	# one BaseFeature + one Cut CombineFeature, a fixed arity `DogboneBaseCombines` can
	# walk positionally on recompute (see its docstring).
	dbFacesByBodyId = {}
	for faceRef in uniqueFaceRefs:
		face = faceRef.entity
		dbFace = DbFace(
			face=face,
			selection=selection,
			params=dbParams,
			commandInputsEdgeSelect=_NullSelectionInput(),
			restoreState=False)
		dbFacesByBodyId.setdefault(hash(dbFace.body.entityToken), []).append(dbFace)

	tempBrepMgr = adsk.fusion.TemporaryBRepManager.get()

	for dbFaces in dbFacesByBodyId.values():
		targetBody = dbFaces[0].body
		baseCombines.addTargetBody(targetBody)

		topFace = None
		if dbParams.fromTop:
			topFace, _ = getTopFace(dbFaces[0].native)

		toolBody = None
		for dbFace in dbFaces:
			for edgeObj in dbFace.selectedEdges:
				edgeToolBody = edgeObj.getToolBody(topFace=topFace)
				if toolBody is None:
					toolBody = edgeToolBody
				else:
					tempBrepMgr.booleanOperation(
						toolBody, edgeToolBody, adsk.fusion.BooleanTypes.UnionBooleanType)

		if toolBody is not None:
			baseCombines.add(targetBody, toolBody)

	return baseCombines


def clamp(value, min, max):
	"""
	Clamps the given `value` to be between the given `min` and `max` values.
	"""
	return min if value < min else max if value > max else value


_htmlTagRegex = re.compile('<[^>]*>')
def withoutHtml(string):
	"""
	Removes all HTML tags from the given `string`.
	"""
	return re.sub(_htmlTagRegex, '', string)
