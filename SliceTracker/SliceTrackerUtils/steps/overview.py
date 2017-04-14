import logging
import os
import ast
import ctk
import qt
import slicer
import vtk
from SlicerProstateUtils.constants import COLOR
from SlicerProstateUtils.decorators import logmethod, onReturnProcessEvents
from SlicerProstateUtils.helpers import IncomingDataMessageBox

from base import SliceTrackerLogicBase, SliceTrackerStep
from plugins.results import SliceTrackerRegistrationResultsPlugin
from plugins.targets import SliceTrackerTargetTablePlugin
from plugins.training import SliceTrackerTrainingPlugin
from plugins.case import SliceTrackerCaseManagerPlugin
from ..constants import SliceTrackerConstants as constants
from ..sessionData import RegistrationResult


class SliceTrackerOverViewStepLogic(SliceTrackerLogicBase):

  def __init__(self):
    super(SliceTrackerOverViewStepLogic, self).__init__()

  def cleanup(self):
    pass

  def applyBiasCorrection(self):
    outputVolume = slicer.vtkMRMLScalarVolumeNode()
    outputVolume.SetName('VOLUME-PREOP-N4')
    slicer.mrmlScene.AddNode(outputVolume)
    params = {'inputImageName': self.session.data.initialVolume.GetID(),
              'maskImageName': self.session.data.initialLabel.GetID(),
              'outputImageName': outputVolume.GetID(),
              'numberOfIterations': '500,400,300'}

    slicer.cli.run(slicer.modules.n4itkbiasfieldcorrection, None, params, wait_for_completion=True)
    self.session.data.initialVolume = outputVolume
    self.session.data.biasCorrectionDone = True


class SliceTrackerOverviewStep(SliceTrackerStep):

  NAME = "Overview"
  LogicClass = SliceTrackerOverViewStepLogic

  def __init__(self):
    super(SliceTrackerOverviewStep, self).__init__()
    self.notifyUserAboutNewData = True

  def cleanup(self):
    self._seriesModel.clear()
    self.trackTargetsButton.setEnabled(False)

  @logmethod(logging.DEBUG)
  def clearData(self):
    self.trackTargetsButton.enabled = False
    self.skipIntraopSeriesButton.enabled = False
    self.updateIntraopSeriesSelectorTable()
    slicer.mrmlScene.Clear(0)

  def setup(self):
    self.caseManagerPlugin = SliceTrackerCaseManagerPlugin()
    self.trainingPlugin = SliceTrackerTrainingPlugin()

    self.trackTargetsButton = self.createButton("Track targets", toolTip="Track targets", enabled=False)
    self.skipIntraopSeriesButton = self.createButton("Skip", toolTip="Skip the currently selected series",
                                                     enabled=False)
    self.setupIntraopSeriesSelector()

    self.setupRegistrationResultsPlugin()

    self.targetTablePlugin = SliceTrackerTargetTablePlugin()
    self.addPlugin(self.targetTablePlugin)

    self.layout().addWidget(self.caseManagerPlugin, 0, 0, 1, 2)
    self.layout().addWidget(self.trainingPlugin, 1, 0, 1, 2)
    self.layout().addWidget(self.targetTablePlugin, 2, 0, 1, 2)
    self.layout().addWidget(self.intraopSeriesSelector, 3, 0, 1, 2)
    self.layout().addWidget(self.regResultsCollapsibleButton, 4, 0, 1, 2)
    self.layout().addWidget(self.trackTargetsButton, 5, 0)
    self.layout().addWidget(self.skipIntraopSeriesButton, 5, 1)
    # self.layout().setRowStretch(8, 1)

  def setupRegistrationResultsPlugin(self):
    self.regResultsCollapsibleButton = ctk.ctkCollapsibleButton()
    self.regResultsCollapsibleButton.collapsed = True
    self.regResultsCollapsibleButton.text = "Registration Evaluation"
    self.regResultsCollapsibleButton.hide()
    self.regResultsCollapsibleLayout= qt.QGridLayout(self.regResultsCollapsibleButton)
    self.regResultsPlugin = SliceTrackerRegistrationResultsPlugin()
    self.regResultsPlugin.resultSelectorVisible = False
    self.regResultsPlugin.titleVisible = False
    self.regResultsPlugin.visualEffectsTitle = ""
    self.regResultsPlugin.registrationTypeButtonsVisible = False
    self.addPlugin(self.regResultsPlugin)
    self.regResultsCollapsibleLayout.addWidget(self.regResultsPlugin)

  def setupIntraopSeriesSelector(self):
    self.intraopSeriesSelector = qt.QComboBox()
    self._seriesModel = qt.QStandardItemModel()
    self.intraopSeriesSelector.setModel(self._seriesModel)

  def setupConnections(self):
    super(SliceTrackerOverviewStep, self).setupConnections()
    self.skipIntraopSeriesButton.clicked.connect(self.onSkipIntraopSeriesButtonClicked)
    self.trackTargetsButton.clicked.connect(self.onTrackTargetsButtonClicked)
    self.intraopSeriesSelector.connect('currentIndexChanged(QString)', self.onIntraopSeriesSelectionChanged)

  def setupSessionObservers(self):
    super(SliceTrackerOverviewStep, self).setupSessionObservers()
    self.session.addEventObserver(self.session.IncomingPreopDataReceiveFinishedEvent, self.onPreopReceptionFinished)
    self.session.addEventObserver(self.session.FailedPreprocessedEvent, self.onFailedPreProcessing)
    self.session.addEventObserver(self.session.RegistrationStatusChangedEvent, self.onRegistrationStatusChanged)
    self.session.addEventObserver(self.session.ZFrameRegistrationSuccessfulEvent, self.onZFrameRegistrationSuccessful)

  def removeSessionEventObservers(self):
    SliceTrackerStep.removeSessionEventObservers(self)
    self.session.removeEventObserver(self.session.IncomingPreopDataReceiveFinishedEvent, self.onPreopReceptionFinished)
    self.session.removeEventObserver(self.session.FailedPreprocessedEvent, self.onFailedPreProcessing)
    self.session.removeEventObserver(self.session.RegistrationStatusChangedEvent, self.onRegistrationStatusChanged)
    self.session.removeEventObserver(self.session.ZFrameRegistrationSuccessfulEvent, self.onZFrameRegistrationSuccessful)

  def onSkipIntraopSeriesButtonClicked(self):
    if slicer.util.confirmYesNoDisplay("Do you really want to skip this series?", windowTitle="Skip series?"):
      self.session.skip(self.intraopSeriesSelector.currentText)
      self.updateIntraopSeriesSelectorTable()

  def onTrackTargetsButtonClicked(self):
    self.session.takeActionForCurrentSeries()

  @logmethod(logging.INFO)
  def onIntraopSeriesSelectionChanged(self, selectedSeries=None):
    self.session.currentSeries = selectedSeries
    if selectedSeries:
      trackingPossible = self.session.isTrackingPossible(selectedSeries)
      self.setIntraopSeriesButtons(trackingPossible, selectedSeries)
      self.configureViewersForSelectedIntraopSeries(selectedSeries)
    self.intraopSeriesSelector.setStyleSheet(self.session.getColorForSelectedSeries())

  def configureViewersForSelectedIntraopSeries(self, selectedSeries):
    if self.session.data.registrationResultWasApproved(selectedSeries) or \
            self.session.data.registrationResultWasRejected(selectedSeries):
      if self.getSetting("COVER_PROSTATE") in selectedSeries and not self.session.data.usePreopData:
        result = self.session.data.getResult(selectedSeries)
        self.currentResult = result.name if result else None
        self.regResultsPlugin.onLayoutChanged()
        self.regResultsCollapsibleButton.hide()
      else:
        self.currentResult = self.session.data.getApprovedOrLastResultForSeries(selectedSeries).name
        self.regResultsCollapsibleButton.show()
        self.regResultsPlugin.onLayoutChanged()
      self.targetTablePlugin.currentTargets = self.currentResult.targets.approved if self.currentResult.approved \
        else self.currentResult.targets.bSpline
    else:
      result = self.session.data.getResult(selectedSeries)
      self.currentResult = result.name if result else None
      self.regResultsPlugin.onLayoutChanged()
      self.regResultsCollapsibleButton.hide()
      if not self.session.data.registrationResultWasSkipped(selectedSeries):
        self.regResultsPlugin.cleanup()
      self.targetTablePlugin.currentTargets = None

  def setIntraopSeriesButtons(self, trackingPossible, selectedSeries):
    trackingPossible = trackingPossible and not self.session.data.completed
    self.trackTargetsButton.setEnabled(trackingPossible)
    self.skipIntraopSeriesButton.setEnabled(trackingPossible and self.session.isEligibleForSkipping(selectedSeries))

  @vtk.calldata_type(vtk.VTK_STRING)
  def onCurrentSeriesChanged(self, caller, event, callData=None):
    logging.info("Current series selection changed invoked from session")
    logging.info("Series with name %s selected" % callData if callData else "")
    if callData:
      model = self.intraopSeriesSelector.model()
      index = next((i for i in range(model.rowCount()) if model.item(i).text() == callData), None)
      self.intraopSeriesSelector.currentIndex = index

  @logmethod(logging.INFO)
  def onZFrameRegistrationSuccessful(self, caller, event):
    self.active = True

  @vtk.calldata_type(vtk.VTK_STRING)
  def onFailedPreProcessing(self, caller, event, callData):
    if slicer.util.confirmYesNoDisplay(callData, windowTitle="SliceTracker"):
      self.startPreProcessingModule()
    else:
      self.session.close()

  def onRegistrationStatusChanged(self, caller, event):
    self.active = True

  def onLoadingMetadataSuccessful(self, caller, event):
    self.active = True

  @vtk.calldata_type(vtk.VTK_STRING)
  def onCaseClosed(self, caller, event, callData):
    if callData != "None":
      slicer.util.infoDisplay(callData, windowTitle="SliceTracker")
    self.clearData()

  def onPreprocessingSuccessful(self, caller, event):
    self.configureRedSliceNodeForPreopData()
    self.promptUserAndApplyBiasCorrectionIfNeeded()

  def onActivation(self):
    super(SliceTrackerOverviewStep, self).onActivation()
    self.updateIntraopSeriesSelectorTable()

  @logmethod(logging.INFO)
  def onPreopReceptionFinished(self, caller=None, event=None):
    if self.invokePreProcessing():
      self.startPreProcessingModule()
    else:
      slicer.util.infoDisplay("No DICOM data could be processed. Please select another directory.",
                              windowTitle="SliceTracker")

  def startPreProcessingModule(self):
    self.setSetting('InputLocation', None, moduleName="mpReview")
    self.layoutManager.selectModule("mpReview")
    mpReview = slicer.modules.mpReviewWidget
    self.setSetting('InputLocation', self.session.preprocessedDirectory, moduleName="mpReview")
    mpReview.onReload()
    slicer.modules.mpReviewWidget.saveButton.clicked.connect(self.returnFromPreProcessingModule)
    self.layoutManager.selectModule(mpReview.moduleName)

  def returnFromPreProcessingModule(self):
    slicer.modules.mpReviewWidget.saveButton.clicked.disconnect(self.returnFromPreProcessingModule)
    self.layoutManager.selectModule(self.MODULE_NAME)
    slicer.mrmlScene.Clear(0)
    self.session.loadPreProcessedData()

  def invokePreProcessing(self):
    if not os.path.exists(self.session.preprocessedDirectory):
      self.logic.createDirectory(self.session.preprocessedDirectory)
    from mpReviewPreprocessor import mpReviewPreprocessorLogic
    self.mpReviewPreprocessorLogic = mpReviewPreprocessorLogic()
    progress = self.createProgressDialog()
    progress.canceled.connect(self.mpReviewPreprocessorLogic.cancelProcess)

    @onReturnProcessEvents
    def updateProgressBar(**kwargs):
      for key, value in kwargs.iteritems():
        if hasattr(progress, key):
          setattr(progress, key, value)

    self.mpReviewPreprocessorLogic.importStudy(self.session.preopDICOMDirectory, progressCallback=updateProgressBar)
    success = False
    if self.mpReviewPreprocessorLogic.patientFound():
      success = True
      self.mpReviewPreprocessorLogic.convertData(outputDir=self.session.preprocessedDirectory, copyDICOM=False,
                                                 progressCallback=updateProgressBar)
    progress.canceled.disconnect(self.mpReviewPreprocessorLogic.cancelProcess)
    progress.close()
    return success

  @vtk.calldata_type(vtk.VTK_STRING)
  def onNewImageSeriesReceived(self, caller, event, callData):
    newImageSeries = ast.literal_eval(callData)
    # self.customStatusProgressBar.text = "New image data has been received."
    self.updateIntraopSeriesSelectorTable()
    selectedSeries = self.intraopSeriesSelector.currentText

    if selectedSeries != "" and self.session.isTrackingPossible(selectedSeries):
      self.takeActionOnSelectedSeries(newImageSeries, selectedSeries)

  def takeActionOnSelectedSeries(self, newImageSeries, selectedSeries):
    if not self.active:
      return
    selectedSeriesNumber = RegistrationResult.getSeriesNumberFromString(selectedSeries)
    newImageSeriesNumbers = [RegistrationResult.getSeriesNumberFromString(s) for s in newImageSeries]
    if self.getSetting("COVER_TEMPLATE") in selectedSeries and not self.session.zFrameRegistrationSuccessful:
      self.onTrackTargetsButtonClicked()
      return

    if selectedSeriesNumber in newImageSeriesNumbers and \
      self.session.isInGeneralTrackable(self.intraopSeriesSelector.currentText):
      if self.notifyUserAboutNewData and not self.session.data.completed:
        dialog = IncomingDataMessageBox()
        self.notifyUserAboutNewDataAnswer, checked = dialog.exec_()
        self.notifyUserAboutNewData = not checked
      if hasattr(self, "notifyUserAboutNewDataAnswer") and self.notifyUserAboutNewDataAnswer == qt.QMessageBox.AcceptRole:
        self.onTrackTargetsButtonClicked()

  def updateIntraopSeriesSelectorTable(self):
    self.intraopSeriesSelector.blockSignals(True)
    currentIndex = self.intraopSeriesSelector.currentIndex
    self._seriesModel.clear()
    for series in self.session.seriesList:
      sItem = qt.QStandardItem(series)
      self._seriesModel.appendRow(sItem)
      color = COLOR.YELLOW
      if self.session.data.registrationResultWasApproved(series) or (self.getSetting("COVER_TEMPLATE") in series and
                                                                   not self.session.isCoverTemplateTrackable(series)):
        color = COLOR.GREEN
      elif self.session.data.registrationResultWasSkipped(series):
        color = COLOR.RED
      elif self.session.data.registrationResultWasRejected(series):
        color = COLOR.GRAY
      self._seriesModel.setData(sItem.index(), color, qt.Qt.BackgroundRole)
    self.intraopSeriesSelector.setCurrentIndex(currentIndex)
    self.intraopSeriesSelector.blockSignals(False)
    self.intraopSeriesSelector.setStyleSheet(self.session.getColorForSelectedSeries(self.intraopSeriesSelector.currentText))
    if self.active:
      self.selectMostRecentEligibleSeries()

  def selectMostRecentEligibleSeries(self):
    substring = self.getSetting("NEEDLE_IMAGE")
    index = -1
    if not self.session.data.getMostRecentApprovedCoverProstateRegistration():
      substring = self.getSetting("COVER_TEMPLATE") \
        if not self.session.zFrameRegistrationSuccessful else self.getSetting("COVER_PROSTATE")
    for item in list(reversed(range(len(self.session.seriesList)))):
      series = self._seriesModel.item(item).text()
      if substring in series:
        if index != -1:
          if self.session.data.registrationResultWasApprovedOrRejected(series) or \
            self.session.data.registrationResultWasSkipped(series):
            break
        index = self.intraopSeriesSelector.findText(series)
        break
      elif self.getSetting("VIBE_IMAGE") in series and index == -1:
        index = self.intraopSeriesSelector.findText(series)
    rowCount = self.intraopSeriesSelector.model().rowCount()
    self.intraopSeriesSelector.setCurrentIndex(index if index != -1 else (rowCount-1 if rowCount else -1))

  def configureRedSliceNodeForPreopData(self):
    if not self.session.data.initialVolume or not self.session.data.initialTargets:
      return
    self.layoutManager.setLayout(constants.LAYOUT_RED_SLICE_ONLY)
    self.redSliceNode.SetOrientationToAxial()
    self.redSliceNode.RotateToVolumePlane(self.session.data.initialVolume)
    self.redSliceNode.SetUseLabelOutline(True)
    self.redCompositeNode.SetLabelOpacity(1)
    self.targetTablePlugin.currentTargets = self.session.data.initialTargets
    # self.logic.centerViewsToProstate()

  def promptUserAndApplyBiasCorrectionIfNeeded(self):
    if not self.session.data.resumed and not self.session.data.completed:
      if slicer.util.confirmYesNoDisplay("Was an endorectal coil used for preop image acquisition?",
                                         windowTitle="SliceTracker"):
        progress = self.createProgressDialog(maximum=2, value=1)
        progress.labelText = '\nBias Correction'
        self.logic.applyBiasCorrection()
        progress.setValue(2)
        progress.close()

    # TODO: self.movingVolumeSelector.setCurrentNode(self.logic.preopVolume)