import adsk.core
import adsk.fusion
from dataclasses import dataclass

from .fusion_util import newObjectCollection

tempBrepMgr = adsk.fusion.TemporaryBRepManager.get()


@dataclass
class _ToolBody:
	targetBody: adsk.fusion.BRepBody
	toolBody: adsk.fusion.BRepBody = None


class DogboneBaseCombines:
	"""
	Tracks, for each target body, one BaseFeature (holding the unioned dogbone-hole
	tool body for that body) plus one CombineFeature that cuts it from the real body.

	Fixed arity of 2 features per target body lets `createOrUpdate` walk
	`existingFeatures` positionally in pairs, the same way BoxJoint's
	`BaseCombines` walks fixed join/intersect triples.
	"""
	def __init__(self):
		self._toolBodiesByTargetBodyId: dict[str, _ToolBody] = {}

	def addTargetBody(self, targetBody: adsk.fusion.BRepBody):
		return self._toolBodiesByTargetBodyId.setdefault(
			targetBody.revisionId, _ToolBody(targetBody))

	def add(self, targetBody: adsk.fusion.BRepBody, toolBody: adsk.fusion.BRepBody):
		entry = self.addTargetBody(targetBody)
		if entry.toolBody is None:
			entry.toolBody = toolBody
		else:
			tempBrepMgr.booleanOperation(
				entry.toolBody, toolBody, adsk.fusion.BooleanTypes.UnionBooleanType)

	def createOrUpdate(self,
		existingFeatures: list[adsk.fusion.Feature],
		allowFeatureCreationAndDeletion: bool
	) -> list[adsk.fusion.Feature]:
		orderedByTargetBodyId: dict[str, _ToolBody] = {}
		existingFeaturesByTargetBodyId: dict[str, tuple[
			adsk.fusion.BaseFeature,  # base feature containing the dogbone tool body
			adsk.fusion.CombineFeature  # cut feature
		]] = {}
		resultingFeatures = []  # existing plus any new features

		# Examine existing features in pairs.
		for baseFeature, cutFeature in zip(*[iter(existingFeatures)] * 2):
			targetBody = cutFeature.targetBody
			targetBodyId = targetBody.revisionId
			entry = self._toolBodiesByTargetBodyId.get(targetBodyId)
			if not entry and allowFeatureCreationAndDeletion:
				# Delete the features associated with the unknown target body.
				cutFeature.deleteMe()
				baseFeature.deleteMe()
			else:
				# Add this target body's computed tool body (if any).
				orderedByTargetBodyId[targetBodyId] = entry or _ToolBody(targetBody)
				existingFeaturesByTargetBodyId.setdefault(targetBodyId, (baseFeature, cutFeature))

		if allowFeatureCreationAndDeletion:
			# Add any target bodies that don't have existing features yet.
			for targetBodyId, entry in self._toolBodiesByTargetBodyId.items():
				orderedByTargetBodyId[targetBodyId] = entry

		design: adsk.fusion.Design = adsk.core.Application.get().activeProduct
		component = design.activeComponent
		baseFeatures = component.features.baseFeatures
		combineFeatures = component.features.combineFeatures

		for targetBodyId, entry in orderedByTargetBodyId.items():
			baseFeature, cutFeature = existingFeaturesByTargetBodyId.get(
				targetBodyId, (None, None))

			if entry.toolBody is None:
				# No corner edges qualified for this target body this time around.
				# Leave any existing features untouched rather than deleting them
				# (allowFeatureCreationAndDeletion may be False), and create nothing new.
				if baseFeature and cutFeature:
					resultingFeatures.extend([baseFeature, cutFeature])
				continue

			# Create or update the BaseFeature holding the dogbone tool body.
			if baseFeature:
				baseFeature.timelineObject.rollTo(rollBefore=False)
				baseFeature.startEdit()
				baseFeature.updateBody(baseFeature.bodies[0], entry.toolBody)
				baseFeature.finishEdit()
			else:
				if resultingFeatures:
					resultingFeatures[-1].timelineObject.rollTo(rollBefore=False)
				baseFeature = baseFeatures.add()
				baseFeature.name = 'dogboneTool'
				baseFeature.startEdit()
				component.bRepBodies.add(entry.toolBody, baseFeature)
				baseFeature.finishEdit()
			resultingFeatures.append(baseFeature)

			# Create the CombineFeature (Cut) if it doesn't exist yet.
			if cutFeature:
				if cutFeature.targetBody != entry.targetBody:
					cutFeature.targetBody = entry.targetBody
			else:
				resultingFeatures[-1].timelineObject.rollTo(rollBefore=False)
				tools = newObjectCollection(baseFeature.bodies)
				combineInput = combineFeatures.createInput(entry.targetBody, tools)
				combineInput.operation = adsk.fusion.FeatureOperations.CutFeatureOperation
				combineInput.isKeepToolBodies = False
				combineInput.isNewComponent = False
				cutFeature = combineFeatures.add(combineInput)
			resultingFeatures.append(cutFeature)

		return resultingFeatures
