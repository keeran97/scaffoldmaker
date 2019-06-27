'''
Utility function for generating tubular mesh from a central line
using a segment profile.
'''
from __future__ import division
import math
from scaffoldmaker.utils.eftfactory_bicubichermitelinear import eftfactory_bicubichermitelinear
from scaffoldmaker.utils.eftfactory_tricubichermite import eftfactory_tricubichermite
from scaffoldmaker.utils import interpolation as interp
from scaffoldmaker.utils import matrix
from scaffoldmaker.utils import vector
from scaffoldmaker.utils import zinc_utils
from opencmiss.zinc.element import Element, Elementbasis
from opencmiss.zinc.field import Field
from opencmiss.zinc.node import Node

def generatetubemesh(region,
    elementsCountAround,
    elementsCountAlongSegment,
    elementsCountThroughWall,
    segmentCountAlong,
    cx, cd1, cd2, cd12,
    xInner, d1Inner, d2Inner, wallThickness,
    segmentAxis, segmentLength,
    useCrossDerivatives,
    useCubicHermiteThroughWall, # or Zinc Elementbasis.FUNCTION_TYPE_LINEAR_LAGRANGE etc.
    annotationGroups, annotationArray, transitElementList, uList, arcLengthOuterMidLength,
    firstNodeIdentifier = 1, firstElementIdentifier = 1
    ):
    '''
    Generates a 3-D tubular mesh with variable numbers of elements
    around, along the central axis, and radially through wall. The
    tubular mesh is created from a segment profile which is mapped onto
    the central line and lateral axes data
    :param elementsCountAround: number of elements around tube
    :param elementsCountAlongSegment: number of elements along segment profile
    :param elementsCountThroughWall: number of elements through wall thickness
    :param segmentCountAlong: number of segments along the tube
    :param cx: coordinates on central line
    :param cd1: derivative along central line
    :param cd2: derivative representing cross axis
    :param cd12: rate of change of cd2 along cd1
    :param xInner: coordinates on inner surface of segment profile
    :param d1Inner: derivatives around inner surface of segment profile
    :param d2Inner: derivatives along inner surface of segment profile
    :param wallThickness: thickness of wall
    :param segmentAxis: axis of segment profile
    :param segmentLength: length of segment profile
    :param useCubicHermiteThroughWall: use linear when false
    :param annotationGroups: Empty when not required
    :param annotationArray: Array storing annotation group name for elements around
    :param transitElementList: True false list of where transition elements are
    located around
    :param uList: List of xi for each node around representative cross-sectional
    profile
    :param arcLengthOuterMidLength: Total arclength of elements around outer surface
    along mid-length of a segment
    :return nodeIdentifier, elementIdentifier
    :return xList, d1List, d2List, d3List: List of coordinates and derivatives
    on tube
    :return sx: List of coordinates sampled from central line
    :return curvatureAlong, factorList: List of curvature and scale factor along mesh
    for each node on inner surface of mesh.
    '''

    zero  = [0.0, 0.0, 0.0]
    elementsCountAlong = elementsCountAlongSegment*segmentCountAlong

    # Sample central line to get same number of elements as elementsCountAlong
    sx, sd1, se, sxi, ssf = interp.sampleCubicHermiteCurves(cx, cd1, elementsCountAlong)
    sd2, _ = interp.interpolateSampleCubicHermite(cd2, cd12, se, sxi, ssf)

    fm = region.getFieldmodule()
    fm.beginChange()
    cache = fm.createFieldcache()
    coordinates = zinc_utils.getOrCreateCoordinateField(fm)

    nodes = fm.findNodesetByFieldDomainType(Field.DOMAIN_TYPE_NODES)
    nodetemplate = nodes.createNodetemplate()
    nodetemplate.defineField(coordinates)
    nodetemplate.setValueNumberOfVersions(coordinates, -1, Node.VALUE_LABEL_VALUE, 1)
    nodetemplate.setValueNumberOfVersions(coordinates, -1, Node.VALUE_LABEL_D_DS1, 1)
    nodetemplate.setValueNumberOfVersions(coordinates, -1, Node.VALUE_LABEL_D_DS2, 1)
    if useCrossDerivatives:
        nodetemplate.setValueNumberOfVersions(coordinates, -1, Node.VALUE_LABEL_D2_DS1DS2, 1)
    if useCubicHermiteThroughWall:
        nodetemplate.setValueNumberOfVersions(coordinates, -1, Node.VALUE_LABEL_D_DS3, 1)
        if useCrossDerivatives:
            nodetemplate.setValueNumberOfVersions(coordinates, -1, Node.VALUE_LABEL_D2_DS1DS3, 1)
            nodetemplate.setValueNumberOfVersions(coordinates, -1, Node.VALUE_LABEL_D2_DS2DS3, 1)
            nodetemplate.setValueNumberOfVersions(coordinates, -1, Node.VALUE_LABEL_D3_DS1DS2DS3, 1)

    mesh = fm.findMeshByDimension(3)

    if useCubicHermiteThroughWall:
        eftfactory = eftfactory_tricubichermite(mesh, useCrossDerivatives)
    else:
        eftfactory = eftfactory_bicubichermitelinear(mesh, useCrossDerivatives)
    eft = eftfactory.createEftBasic()

    elementtemplate = mesh.createElementtemplate()
    elementtemplate.setElementShapeType(Element.SHAPE_TYPE_CUBE)
    result = elementtemplate.defineField(coordinates, -1, eft)

    # create nodes
    nodeIdentifier = firstNodeIdentifier
    x = [ 0.0, 0.0, 0.0 ]
    dx_ds1 = [ 0.0, 0.0, 0.0 ]
    dx_ds2 = [ 0.0, 0.0, 0.0 ]
    dx_ds3 = [ 0.0, 0.0, 0.0 ]
    xInnerList = []
    d1InnerList = []
    d2InnerList = []
    d3InnerUnitList = []
    xList = []
    dx_ds1List = []
    dx_ds2List = []
    dx_ds3List = []
    curvatureAlong = []
    smoothd2Raw = []
    smoothd2InnerList = []
    d1List = []

# Map each face along segment profile to central line
    for nSegment in range(segmentCountAlong):
        for nAlongSegment in range(elementsCountAlongSegment + 1):
            n2 = nSegment*elementsCountAlongSegment + nAlongSegment
            if nSegment == 0 or (nSegment > 0 and nAlongSegment > 0):
                # Rotate to align segment axis with tangent of central line
                segmentMid = [0.0, 0.0, segmentLength/elementsCountAlongSegment* nAlongSegment]
                unitTangent = vector.normalise(sd1[n2])
                cp = vector.crossproduct3(segmentAxis, unitTangent)
                if vector.magnitude(cp)> 0.0:
                    axisRot = vector.normalise(cp)
                    thetaRot = math.acos(vector.dotproduct(segmentAxis, unitTangent))
                    rotFrame = matrix.getRotationMatrixFromAxisAngle(axisRot, thetaRot)
                    midRot = [rotFrame[j][0]*segmentMid[0] + rotFrame[j][1]*segmentMid[1] + rotFrame[j][2]*segmentMid[2] for j in range(3)]
                    translateMatrix = [sx[n2][j] - midRot[j] for j in range(3)]
                else:
                    midRot = segmentMid

                for n1 in range(elementsCountAround):
                    n = nAlongSegment*elementsCountAround + n1
                    x = xInner[n]
                    d1 = d1Inner[n]
                    d2 = d2Inner[n]
                    if vector.magnitude(cp)> 0.0:
                        xRot1 = [rotFrame[j][0]*x[0] + rotFrame[j][1]*x[1] + rotFrame[j][2]*x[2] for j in range(3)]
                        d1Rot1 = [rotFrame[j][0]*d1[0] + rotFrame[j][1]*d1[1] + rotFrame[j][2]*d1[2] for j in range(3)]
                        d2Rot1 = [rotFrame[j][0]*d2[0] + rotFrame[j][1]*d2[1] + rotFrame[j][2]*d2[2] for j in range(3)]
                        if n1 == 0:
                            # Project sd2 onto plane normal to sd1
                            v = sd2[n2]
                            pt = [midRot[j] + sd2[n2][j] for j in range(3)]
                            dist = vector.dotproduct(v, unitTangent)
                            ptOnPlane = [pt[j] - dist*unitTangent[j] for j in range(3)]
                            newVector = [ptOnPlane[j] - midRot[j] for j in range(3)]
                            # Rotate first point to align with planar projection of sd2
                            firstVector = vector.normalise([xRot1[j] - midRot[j] for j in range(3)])
                            thetaRot2 = math.acos(vector.dotproduct(vector.normalise(newVector), firstVector))
                            cp2 = vector.crossproduct3(vector.normalise(newVector), firstVector)
                            if vector.magnitude(cp2) > 0.0:
                                cp2 = vector.normalise(cp2)
                                signThetaRot2 = vector.dotproduct(unitTangent, cp2)
                                axisRot2 = unitTangent
                                rotFrame2 = matrix.getRotationMatrixFromAxisAngle(axisRot2, -signThetaRot2*thetaRot2)
                            else:
                                rotFrame2 = [ [1, 0, 0], [0, 1, 0], [0, 0, 1]]
                        xRot2 = [rotFrame2[j][0]*xRot1[0] + rotFrame2[j][1]*xRot1[1] + rotFrame2[j][2]*xRot1[2] for j in range(3)]
                        d1Rot2 = [rotFrame2[j][0]*d1Rot1[0] + rotFrame2[j][1]*d1Rot1[1] + rotFrame2[j][2]*d1Rot1[2] for j in range(3)]
                        d2Rot2 = [rotFrame2[j][0]*d2Rot1[0] + rotFrame2[j][1]*d2Rot1[1] + rotFrame2[j][2]*d2Rot1[2] for j in range(3)]
                    else:
                        xRot2 = x
                        d1Rot2 = d1
                        d2Rot2 = d2
                    xTranslate = [xRot2[j] + translateMatrix[j] for j in range(3)]
                    xInnerList.append(xTranslate)
                    d1InnerList.append(d1Rot2)
                    d2InnerList.append(d2Rot2)
                    d3Unit = vector.normalise(vector.crossproduct3(vector.normalise(d1Rot2), vector.normalise(d2Rot2)))
                    d3InnerUnitList.append(d3Unit)

    for n1 in range(elementsCountAround):
        nx = []
        nd2 = []
        for n2 in range(elementsCountAlong + 1):
            n = n2*elementsCountAround + n1
            nx.append(xInnerList[n])
            nd2.append(d2InnerList[n])
        smoothd2 = interp.smoothCubicHermiteDerivativesLine(nx, nd2)
        smoothd2Raw.append(smoothd2)

    for n2 in range(elementsCountAlong + 1):
        for n1 in range(elementsCountAround):
            smoothd2InnerList.append(smoothd2Raw[n1][n2])
            n = elementsCountAround * n2 + n1
            if n2 == 0:
                curvature = interp.getCubicHermiteCurvatureSimple(sx[n2], sd1[n2], sx[n2+1], sd1[n2+1], 0.0)
            elif n2 == elementsCountAlong:
                curvature = interp.getCubicHermiteCurvatureSimple(sx[n2-1], sd1[n2-1], sx[n2], sd1[n2], 1.0)
            else:
                curvature = 0.5*(
                    interp.getCubicHermiteCurvatureSimple(sx[n2-1], sd1[n2-1], sx[n2], sd1[n2], 1.0) +
                    interp.getCubicHermiteCurvatureSimple(sx[n2], sd1[n2], sx[n2+1], sd1[n2+1], 0.0))
            curvatureAlong.append(curvature)

    # Pre-calculate node locations and derivatives on outer boundary
    xOuterList, curvatureInner = getOuterCoordinatesAndCurvatureFromInner(xInnerList, d1InnerList, d3InnerUnitList, wallThickness, elementsCountAlong, elementsCountAround, transitElementList)

    # Interpolate to get nodes through wall
    for n3 in range(elementsCountThroughWall + 1):
        xi3 = 1/elementsCountThroughWall * n3
        x, dx_ds1, dx_ds2, dx_ds3, factorList = interpolatefromInnerAndOuter( xInnerList, xOuterList,
            wallThickness, xi3, sx, curvatureInner, curvatureAlong, d1InnerList, smoothd2InnerList, d3InnerUnitList,
            elementsCountAround, elementsCountAlong, elementsCountThroughWall)
        xList = xList + x
        dx_ds1List = dx_ds1List + dx_ds1
        dx_ds2List = dx_ds2List + dx_ds2
        dx_ds3List = dx_ds3List + dx_ds3

    for n in range(len(xList)):
        node = nodes.createNode(nodeIdentifier, nodetemplate)
        cache.setNode(node)
        coordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_VALUE, 1, xList[n])
        coordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_D_DS1, 1, dx_ds1List[n])
        coordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_D_DS2, 1, dx_ds2List[n])
        coordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_D_DS3, 1, dx_ds3List[n])
        if useCrossDerivatives:
                coordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_D2_DS1DS2, 1, zero)
                coordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_D2_DS1DS3, 1, zero)
                coordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_D2_DS2DS3, 1, zero)
                coordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_D3_DS1DS2DS3, 1, zero)
        # print('NodeIdentifier = ', nodeIdentifier, xList[n])
        nodeIdentifier = nodeIdentifier + 1

    # # For debugging - Nodes along central line
    # for pt in range(len(sx)):
        # node = nodes.createNode(nodeIdentifier, nodetemplate)
        # cache.setNode(node)
        # coordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_VALUE, 1, sx[pt])
        # coordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_D_DS1, 1, sd1[pt])
        # coordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_D_DS2, 1, vector.normalise(sd2[pt]))
        # coordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_D_DS3, 1, vector.normalise(testRot[pt]))
        # nodeIdentifier = nodeIdentifier + 1

    # create elements
    elementIdentifier = firstElementIdentifier
    now = (elementsCountAlong + 1)*elementsCountAround
    for e3 in range(elementsCountThroughWall):
        for e2 in range(elementsCountAlong):
            for e1 in range(elementsCountAround):
                element = mesh.createElement(elementIdentifier, elementtemplate)
                bni11 = e3*now + e2*elementsCountAround + e1 + 1
                bni12 = e3*now + e2*elementsCountAround + (e1 + 1) % elementsCountAround + 1
                bni21 = e3*now + (e2 + 1)*elementsCountAround + e1 + 1
                bni22 = e3*now + (e2 + 1)*elementsCountAround + (e1 + 1) % elementsCountAround + 1
                nodeIdentifiers = [ bni11, bni12, bni21, bni22, bni11 + now, bni12 + now, bni21 + now, bni22 + now ]
                result = element.setNodesByIdentifier(eft, nodeIdentifiers)
                elementIdentifier = elementIdentifier + 1
                if annotationGroups:
                    for annotationGroup in annotationGroups:
                        if annotationArray[e1] == annotationGroup._name:
                            meshGroup = annotationGroup.getMeshGroup(mesh)
                            meshGroup.addElement(element)

    # Define texture coordinates field
    textureCoordinates = zinc_utils.getOrCreateTextureCoordinateField(fm)
    textureNodetemplate1 = nodes.createNodetemplate()
    textureNodetemplate1.defineField(textureCoordinates)
    textureNodetemplate1.setValueNumberOfVersions(textureCoordinates, -1, Node.VALUE_LABEL_VALUE, 1)
    textureNodetemplate1.setValueNumberOfVersions(textureCoordinates, -1, Node.VALUE_LABEL_D_DS1, 1)
    textureNodetemplate1.setValueNumberOfVersions(textureCoordinates, -1, Node.VALUE_LABEL_D_DS2, 1)
    if useCrossDerivatives:
        textureNodetemplate1.setValueNumberOfVersions(textureCoordinates, -1, Node.VALUE_LABEL_D2_DS1DS2, 1)

    textureNodetemplate2 = nodes.createNodetemplate()
    textureNodetemplate2.defineField(textureCoordinates)
    textureNodetemplate2.setValueNumberOfVersions(textureCoordinates, -1, Node.VALUE_LABEL_VALUE, 2)
    textureNodetemplate2.setValueNumberOfVersions(textureCoordinates, -1, Node.VALUE_LABEL_D_DS1, 2)
    textureNodetemplate2.setValueNumberOfVersions(textureCoordinates, -1, Node.VALUE_LABEL_D_DS2, 2)
    if useCrossDerivatives:
        textureNodetemplate2.setValueNumberOfVersions(textureCoordinates, -1, Node.VALUE_LABEL_D2_DS1DS2, 2)

    bicubichermitelinear = eftfactory_bicubichermitelinear(mesh, useCrossDerivatives)
    eftTexture1 = bicubichermitelinear.createEftBasic()

    elementtemplate1 = mesh.createElementtemplate()
    elementtemplate1.setElementShapeType(Element.SHAPE_TYPE_CUBE)
    elementtemplate1.defineField(textureCoordinates, -1, eftTexture1)

    eftTexture2 = bicubichermitelinear.createEftOpenTube()
    elementtemplate2 = mesh.createElementtemplate()
    elementtemplate2.setElementShapeType(Element.SHAPE_TYPE_CUBE)
    elementtemplate2.defineField(textureCoordinates, -1, eftTexture2)

    # Calculate texture coordinates and derivatives
    d2 = [0.0, 1.0 / elementsCountAlong, 0.0]

    for n1 in range(len(uList)):
        d1 = [uList[n1] - uList[n1-1] if n1 > 0 else uList[n1+1] - uList[n1],
              0.0,
              0.0]
        d1List.append(d1)

    # To modify derivative along transition elements
    for i in range(len(transitElementList)):
        if transitElementList[i]:
            d1List[i+1] = d1List[i+2]

    nodeIdentifier = firstNodeIdentifier
    for n3 in range(elementsCountThroughWall + 1):
        for n2 in range(elementsCountAlong + 1):
            for n1 in range(elementsCountAround):
                u = [ uList[n1],
                      1.0 / elementsCountAlong * n2,
                      1.0 / elementsCountThroughWall * n3]
                d1 = d1List[n1]
                node = nodes.findNodeByIdentifier(nodeIdentifier)
                node.merge(textureNodetemplate2 if n1 == 0 else textureNodetemplate1)
                cache.setNode(node)
                textureCoordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_VALUE, 1, u)
                textureCoordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_D_DS1, 1, d1)
                textureCoordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_D_DS2, 1, d2)
                if useCrossDerivatives:
                    textureCoordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_D2_DS1DS2, 1, zero)
                if n1 == 0:
                    u = [ 1.0, 1.0 / elementsCountAlong * n2, 1.0 / elementsCountThroughWall * n3]
                    d1 = d1List[-1]
                    textureCoordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_VALUE, 2, u)
                    textureCoordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_D_DS1, 2, d1)
                    textureCoordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_D_DS2, 2, d2)
                    if useCrossDerivatives:
                        textureCoordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_D2_DS1DS2, 2, zero)
                nodeIdentifier = nodeIdentifier + 1

    # Define flat coordinates field
    flatCoordinates = zinc_utils.getOrCreateFlatCoordinateField(fm)
    flatNodetemplate1 = nodes.createNodetemplate()
    flatNodetemplate1.defineField(flatCoordinates)
    flatNodetemplate1.setValueNumberOfVersions(flatCoordinates, -1, Node.VALUE_LABEL_VALUE, 1)
    flatNodetemplate1.setValueNumberOfVersions(flatCoordinates, -1, Node.VALUE_LABEL_D_DS1, 1)
    flatNodetemplate1.setValueNumberOfVersions(flatCoordinates, -1, Node.VALUE_LABEL_D_DS2, 1)
    if useCrossDerivatives:
        flatNodetemplate1.setValueNumberOfVersions(flatCoordinates, -1, Node.VALUE_LABEL_D2_DS1DS2, 1)

    flatNodetemplate2 = nodes.createNodetemplate()
    flatNodetemplate2.defineField(flatCoordinates)
    flatNodetemplate2.setValueNumberOfVersions(flatCoordinates, -1, Node.VALUE_LABEL_VALUE, 2)
    flatNodetemplate2.setValueNumberOfVersions(flatCoordinates, -1, Node.VALUE_LABEL_D_DS1, 2)
    flatNodetemplate2.setValueNumberOfVersions(flatCoordinates, -1, Node.VALUE_LABEL_D_DS2, 2)
    if useCrossDerivatives:
        flatNodetemplate2.setValueNumberOfVersions(flatCoordinates, -1, Node.VALUE_LABEL_D2_DS1DS2, 2)

    flatElementtemplate1 = mesh.createElementtemplate()
    flatElementtemplate1.setElementShapeType(Element.SHAPE_TYPE_CUBE)
    flatElementtemplate1.defineField(flatCoordinates, -1, eftTexture1)

    flatElementtemplate2 = mesh.createElementtemplate()
    flatElementtemplate2.setElementShapeType(Element.SHAPE_TYPE_CUBE)
    flatElementtemplate2.defineField(flatCoordinates, -1, eftTexture2)

    # Calculate texture coordinates and derivatives
    totalLengthAlong = segmentLength*segmentCountAlong
    d2 = [0.0, totalLengthAlong/elementsCountAlong, 0.0]

    d1List = []
    for n1 in range(len(uList)):
        d1 = [(uList[n1] - uList[n1-1])*arcLengthOuterMidLength if n1 > 0 else (uList[n1+1] - uList[n1])*arcLengthOuterMidLength,
              0.0,
              0.0]
        d1List.append(d1)

    # To modify derivative along transition elements
    for i in range(len(transitElementList)):
        if transitElementList[i]:
            d1List[i+1] = d1List[i+2]

    nodeIdentifier = firstNodeIdentifier
    for n3 in range(elementsCountThroughWall + 1):
        for n2 in range(elementsCountAlong + 1):
            for n1 in range(elementsCountAround):
                x = [ uList[n1]*arcLengthOuterMidLength,
                      totalLengthAlong / elementsCountAlong * n2,
                      wallThickness / elementsCountThroughWall * n3]
                d1 = d1List[n1]
                node = nodes.findNodeByIdentifier(nodeIdentifier)
                node.merge(flatNodetemplate2 if n1 == 0 else flatNodetemplate1)
                cache.setNode(node)
                flatCoordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_VALUE, 1, x)
                flatCoordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_D_DS1, 1, d1)
                flatCoordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_D_DS2, 1, d2)
                if useCrossDerivatives:
                    flatCoordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_D2_DS1DS2, 1, zero)
                if n1 == 0:
                    x = [ arcLengthOuterMidLength, totalLengthAlong / elementsCountAlong * n2, wallThickness / elementsCountThroughWall * n3]
                    d1 = d1List[-1]
                    flatCoordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_VALUE, 2, x)
                    flatCoordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_D_DS1, 2, d1)
                    flatCoordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_D_DS2, 2, d2)
                    if useCrossDerivatives:
                        flatCoordinates.setNodeParameters(cache, -1, Node.VALUE_LABEL_D2_DS1DS2, 2, zero)
                nodeIdentifier = nodeIdentifier + 1

    # Define flat coordinates field & texture coordinates field over elements
    elementIdentifier = firstElementIdentifier
    now = (elementsCountAlong + 1)*elementsCountAround

    for e3 in range(elementsCountThroughWall):
        for e2 in range(elementsCountAlong):
            for e1 in range(elementsCountAround):
                onOpening = e1 > elementsCountAround - 2
                element = mesh.findElementByIdentifier(elementIdentifier)
                element.merge(elementtemplate2 if onOpening else elementtemplate1)
                element.merge(flatElementtemplate2 if onOpening else flatElementtemplate1)
                bni11 = e3*now + e2*elementsCountAround + e1 + 1
                bni12 = e3*now + e2*elementsCountAround + (e1 + 1) % elementsCountAround + 1
                bni21 = e3*now + (e2 + 1)*elementsCountAround + e1 + 1
                bni22 = e3*now + (e2 + 1)*elementsCountAround + (e1 + 1) % elementsCountAround + 1
                nodeIdentifiers = [ bni11, bni12, bni21, bni22, bni11 + now, bni12 + now, bni21 + now, bni22 + now ]
                element.setNodesByIdentifier(eftTexture2 if onOpening else eftTexture1, nodeIdentifiers)
                elementIdentifier = elementIdentifier + 1

    fm.endChange()

    return annotationGroups, nodeIdentifier, elementIdentifier, xList, dx_ds1List, dx_ds2List, dx_ds3List, sx, curvatureAlong, factorList

def getOuterCoordinatesAndCurvatureFromInner(xInner, d1Inner, d3Inner, wallThickness, elementsCountAlong, elementsCountAround, transitElementList):
    """
    Generates coordinates on outer surface and curvature of inner
    surface from coordinates and derivatives of inner surface using
    wall thickness and normals.
    param xInner: Coordinates on inner surface
    param d1Inner: Derivatives on inner surface around tube
    param d3Inner: Derivatives on inner surface through wall
    param wallThickness: Thickness of wall
    param elementsCountAlong: Number of elements along tube
    param elementsCountAround: Number of elements around tube
    return xOuter: Coordinates on outer surface
    return curvatureInner: Curvature of coordinates on inner surface
    """
    xOuter = []
    curvatureInner = []
    for n2 in range(elementsCountAlong + 1):
        for n1 in range(elementsCountAround):
            n = n2*elementsCountAround + n1
            x = [xInner[n][i] + d3Inner[n][i]*wallThickness for i in range(3)]
            prevIdx = n - 1 if (n1 != 0) else (n2 + 1)*elementsCountAround - 1
            nextIdx = n + 1 if (n1 < elementsCountAround - 1) else n2*elementsCountAround
            norm = d3Inner[n]
            kappam = interp.getCubicHermiteCurvatureSimple(xInner[prevIdx], d1Inner[prevIdx], xInner[n], d1Inner[n], 1.0)
            kappap = interp.getCubicHermiteCurvatureSimple(xInner[n], d1Inner[n], xInner[nextIdx], d1Inner[nextIdx], 0.0)
            if not transitElementList[n1] and not transitElementList[(n1-1)%elementsCountAround]:
                curvatureAround = 0.5*(kappam + kappap)
            elif transitElementList[n1]:
                curvatureAround = kappam
            elif transitElementList[(n1-1)%elementsCountAround]:
                curvatureAround = kappap
            xOuter.append(x)
            curvatureInner.append(curvatureAround)

    return xOuter, curvatureInner

def interpolatefromInnerAndOuter( xInner, xOuter, thickness, xi3, sx, curvatureInner, curvatureAlong,
    d1Inner, d2Inner, d3InnerUnit, elementsCountAround, elementsCountAlong, elementsCountThroughWall):
    """
    Generate coordinates and derivatives at xi3 by interpolating with 
    inner and outer coordinates and derivatives.
    :param xInner: Coordinates on inner surface
    :param xOuter: Coordinates on outer surface
    :param thickness: Thickness of wall
    :param sx: List of coordinates sampled from central line
    :param curvatureInner: Curvature of coordinates on inner surface
    :param curvatureAlong: Curvature of coordinates on inner surface along mesh
    :param d1Inner: Derivatives on inner surface around tube
    :param d2Inner: Derivatives on inner surface along tube
    :param d3InnerUnit: Unit derivatives on inner surface through wall
    :param elementsCountAround: Number of elements around tube
    :param elementsCountAlong: Number of elements along tube
    :param elementsCountThroughWall: Number of elements through wall
    :return xList, dx_ds1List, dx_ds2List, dx_ds3List: Coordinates and derivatives on xi3
    :return factorList: List of factors used for scaling d2
    """
    xList = []
    dx_ds1List = []
    dx_ds2List = []
    dx_ds3List =[]
    factorList = []

    for n2 in range(elementsCountAlong + 1):
        for n1 in range(elementsCountAround):
            n = n2*elementsCountAround + n1
            norm = d3InnerUnit[n]
            # x
            innerx = xInner[n]
            outerx = xOuter[n]
            dWall = [thickness*c for c in norm]
            x = interp.interpolateCubicHermite(innerx, dWall, outerx, dWall, xi3)
            xList.append(x)
            # dx_ds1
            factor = 1.0 + thickness*xi3 * curvatureInner[n]
            dx_ds1 = [ factor*c for c in d1Inner[n]]
            dx_ds1List.append(dx_ds1)
            # dx_ds2
            curvature = curvatureAlong[n]
            distance = vector.magnitude([x[i] - sx[n2][i] for i in range(3)])
            factor = 1.0 + curvature*distance
            dx_ds2 = [ factor*c for c in d2Inner[n]]
            dx_ds2List.append(dx_ds2)
            factorList.append(factor)

            #dx_ds3
            dx_ds3 = [c * thickness/elementsCountThroughWall for c in norm]
            dx_ds3List.append(dx_ds3)

    return xList, dx_ds1List, dx_ds2List, dx_ds3List, factorList
