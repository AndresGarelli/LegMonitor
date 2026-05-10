print("Loading LegM...")
print("Importing libraries")
import sys
import time
import json
import numpy as np
import os
from pathlib import Path
from datetime import datetime
from enum import Enum, auto
import cv2
import pandas as pd
from utils.temperature_monitor import TemperatureMonitor
from utils.graphEvent1h import Graph_1h
from utils.graphEventPi import makeGraph
import threading


from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QLabel, QWidget, QVBoxLayout, QPushButton, QHBoxLayout,
    QInputDialog, QDoubleSpinBox, QSpinBox, QFrame, QGroupBox, QGridLayout, QCheckBox
)
from PyQt5.QtCore import QThread, pyqtSignal, QPointF, QObject, pyqtSlot, Qt, QElapsedTimer, QTimer
from PyQt5.QtGui import QImage, QPixmap, QPainter,QPen, QColor,QFont


from picamera2 import Picamera2, MappedArray
from libcamera import Transform
try:
    from picamera2.outputs import FileOutput
    from picamera2.encoders import H264Encoder
except ImportError:
    #On some versions of picamera2/libcamera it might be structured differently
    print("Check picamera2 installation for Encoder/Output classes")
    
SCRIPT_DIR = Path(__file__).resolve().parent



#move this function to utils.py later and import it

def default_experiment_name():
    return datetime.now().strftime("%Y%m%d_%H%M%S")



class ExperimentState(Enum):
    SETUP = auto()
    RUN= auto()
    IDLE = auto()


class GraphWorker(QObject):
    finished = pyqtSignal()
    def __init__(self, experiment_dir, experiment_name):
        super().__init__()
        self.experiment_dir = experiment_dir
        self.experiment_name = experiment_name

    @pyqtSlot()
    def run(self):
        from utils.graphEventPi import makeGraph
        try:
            makeGraph(self.experiment_dir, self.experiment_name)
        finally:
            self.finished.emit() # Signal sent regardless of success/fail

class AnalysisWorker(QObject):
    finished = pyqtSignal()
    graph_ready = pyqtSignal(QImage)
    
    def __init__(self, resolution, parent=None):
        super().__init__(parent)
        self.state = ExperimentState.SETUP
        self.resolution = resolution # (w,h)
        self.last_frame = None
        self.monitor = None
        self.live_graph = None
        self.csv_path = None
        self.totalFrames = None
        
    
    @pyqtSlot(list,str, object)
    def setup_experiment(self,rois, csv_path, monitor):
        """Prepare mask and CSV header before starting"""
        self.rois = rois
        self.csv_path = csv_path
        self.monitor = monitor
        self.contourThreshold = 0
        self.totalFrames = 0
        self.masks =[]
        self.mask_pixel_counts = []
        w, h = self.resolution
        roi_names = [f"ROI_{roi.name}" for roi in self.rois] 
        
        for roi in self.rois:
            mask = np.zeros((h, w), dtype=np.uint8)
            points = np.array([[p.x(), p.y()] for p in roi.points], dtype=np.int32)
            cv2.fillPoly(mask,[points],255)
            self.masks.append(mask)
            self.mask_pixel_counts.append(max(1,cv2.countNonZero(mask)))
            #roi_names.append(f"ROI_{roi_names}")

        self.column_names = ["time","temp", "SP", "mode", "relay"]+ roi_names

        self.live_graph = Graph_1h(len(self.rois), roi_names, self.monitor)
        
    
    @pyqtSlot(object)
    def process_frame(self, frame):
        if self.state != ExperimentState.RUN or self.live_graph is None:
            return
        
        if self.monitor is not None:
            status= self.monitor.get_status()
            temp = status["temperature"] if status["sensor_found"] else 25.0
            sp = status["setpoint"] if status["setpoint"] else 25.0
            mode = status["mode"] #if status["mode"] else "NO_SENSOR"
            relay = 1 if status["heating"] else 0
#         else:
#             temp = 25
#             sp = 25
#             mode = "NO_SENSOR"
#             relay = 0
        #timestamp = datetime.now().strftime("%H:%M:%S")
       
        self.totalFrames += 1
        gray = frame[:,:,1] #Green channel in BGR
        if self.last_frame is None:
            self.last_frame = gray
            #return  #remove return from here. I want to compute first frame as 0, important when comparing video and results
            

        diff = cv2.absdiff(gray, self.last_frame)
        _ ,thresh = cv2.threshold(diff,10,255,cv2.THRESH_BINARY)

        cnts = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]
        """countour area of single pixels or straight lines is 0. An L-shaped group of 3 px has an area of 0.5
            >0 filters out single points and straight lines
            """
        
        filtered_contours = [cnt for cnt in cnts if cv2.contourArea(cnt) > self.contourThreshold]
        black = np.zeros((self.resolution[1], self.resolution[0]),dtype=np.int8) #np array shape is rows,column
        cv2.drawContours(black,filtered_contours,-1,255,-1)

        roi_results = []
        for i, mask in enumerate(self.masks):
            motion_area = cv2.bitwise_and(black, black, mask=mask)
            percent = (cv2.countNonZero(motion_area) * 100.0) / self.mask_pixel_counts[i]
            roi_results.append(percent)

        # save to .csv --> está enviando timestamp y no totalFrames como antes.
        full_row = [self.totalFrames, temp, sp, mode, relay] + roi_results
       
        df = pd.DataFrame([full_row], columns=self.column_names)
        df.to_csv(self.csv_path, mode='a', header=not os.path.exists(self.csv_path), index=False)
   
        # 4. Update Graph
        # Graph_1h.update expects: [totalFrames, temp, SP, mode, relay, ...rois]
        # We'll use 0 for totalFrames as it's handled internally by your Graph class
        img_bytes = self.live_graph.update([0, temp, sp, mode, relay] + roi_results)
        # 5. Emit Graph Image
        if img_bytes:
            qimg = QImage.fromData(img_bytes)
            self.graph_ready.emit(qimg)

        self.last_frame = gray
        
        
    def run_analysis(self,frame):
        #placeholder
        pass
    
    @pyqtSlot()    
    def stop(self):
        self.finished.emit()
        




class PolygonROI:
    def __init__(self,name):
        self.name = name
        self.points = [] #list of QPointF


        
class ImageDisplayWidget(QWidget):
    def __init__(self, resolution, parent=None):
        super().__init__(parent)
        self.image_w, self.image_h  = resolution
        self.frame = None
        self.rois = []
        self.current_roi = None
        self.drawing_enabled = True
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet("background-color: black;")
        #self.setMinimumSize(self.image_w, self.image_h)
        self.temp = 0.0
        self.setpoint = 0.0
        self.is_heating = False
        self.sensor_found = False
        self.setMinimumSize(640,360)
    # -----------------------------
    # Frame handling
    # -----------------------------
    def set_frame(self, frame):
        self.frame = frame
        self.update()


    # ------------------
    # Temperature display
    #-------------------
 
    def set_status(self, temp, sp, heating, sensor_found,mode,phase):
        """Update the metadata values for the next paint cycle"""
        self.temp = temp
        self.setpoint = sp
        self.is_heating = heating
        self.sensor_found = sensor_found
        self.phase = phase
        # We don't need to call self.update() here because 
        # set_frame() is likely being called 10 times a second anyway.

    def draw_hardware_overlay(self, painter):
        # Set a semi-transparent black background for the text for readability
        overlay_font = QFont("Arial", 14, QFont.Bold)
        painter.setFont(overlay_font)

        # Position text in the top left
        margin_x = 100
        margin_y = 15
        
        if self.sensor_found:
            if isinstance(self.setpoint, (int,float)):
                sp_text = f"{self.setpoint}"
            else:
                sp_text = "--"
            status_text = f"SP: {sp_text}   TEMP: {self.temp:.1f} °C"
            color = QColor(250, 250, 250) # 
        else:
            status_text = "SENSOR NOT FOUND"
            color = QColor(255, 0, 0) # Red

        # Draw Text with a small drop shadow for visibility
        painter.setPen(QColor(0, 0, 0)) # Shadow
        painter.drawText(margin_x + 2, margin_y + 2, status_text)
        painter.setPen(color)
        painter.drawText(margin_x , margin_y, status_text)

        # Draw Heating Indicator (Red circle in top right)
        if self.sensor_found:
            if self.is_heating:
                painter.setBrush(QColor(255, 0, 0))
                painter.setPen(Qt.NoPen)
                indicator_size = 12
                # Position relative to widget width
                painter.drawEllipse(self.width() - indicator_size - margin_y, margin_y, 
                                    indicator_size, indicator_size)
            else:
                painter.setBrush(QColor(200, 200, 200))
                painter.setPen(Qt.NoPen)
                indicator_size = 12
                # Position relative to widget width
                painter.drawEllipse(self.width() - indicator_size - margin_y, margin_y, 
                                    indicator_size, indicator_size)

    #----------------
    #  ROI scalling
    #----------------

    def get_image_coords(self, widget_pos):
        #Maps a click on the widget to the actual pixel in the 1344x752 imag
        #This handles cases where the image is resized
        ratio_w = self.image_w / self.width()
        ratio_h = self.image_h / self.height()
        return QPointF(widget_pos.x() * ratio_w, widget_pos.y() * ratio_h)

    def get_widget_coords(self, image_pos):
        #Converts Camera image pixels to Widget pixls
        #This handles cases where the widget is is resized
        ratio_w = self.width() / self.image_w
        ratio_h = self.height() / self.image_h
        return QPointF(image_pos.x() * ratio_w, image_pos.y() * ratio_h)
    
    # -----------------------------
    # Painting
    # -----------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing,True)

        if self.frame is not None:
            h, w, _ = self.frame.shape
            qimg = QImage(self.frame.data, w,h, 3 * w, QImage.Format_BGR888)
            painter.drawImage(self.rect(), qimg)

        # Draw completed ROIs
        pen = QPen(QColor(0, 255, 0), 1)
        label_pen = QPen(QColor(0,0,0),2)
        
        
        font = QFont()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        
        for roi in self.rois:
            if len(roi.points) > 1:
                #SCale back point from image coordinate to widget coordinates
                scaled_points = [self.get_widget_coords(p) for p in roi.points]
                painter.setPen(pen)
                painter.drawPolygon(*scaled_points)
                label_pos = scaled_points[0]# roi.points[0]
                painter.setPen(label_pen)
                painter.drawText(label_pos + QPointF(5, -5), roi.name)
                #painter.drawText(scaled_points[0] + QPointF(5, -5), roi.name)

        # Draw current ROI
        if self.current_roi and len(self.current_roi.points) > 0:
            pen = QPen(QColor(255, 0, 0), 2, Qt.DashLine)
            painter.setPen(pen)
            scaled_points = [self.get_widget_coords(p) for p in self.current_roi.points]
            painter.drawPolyline(*scaled_points)

        self.draw_hardware_overlay(painter)
    
    # -----------------------------
    # Mouse events
    # -----------------------------
    def mousePressEvent(self, event):
        if not self.drawing_enabled:
            return

        if event.button() == Qt.LeftButton:
            if self.current_roi is None:
                self.current_roi = PolygonROI(
                    name=f"{len(self.rois)}"
                )
##            self.current_roi.points.append(QPointF(event.pos()))
##            self.update()

            img_pos = self.get_image_coords(event.pos())
            self.current_roi.points.append(img_pos)
            self.update()

        elif event.button() == Qt.RightButton:
            self.finish_current_roi()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.current_roi = None
            self.update()

    # -----------------------------
    # ROI control
    # -----------------------------
    def finish_current_roi(self):
        if self.current_roi and len(self.current_roi.points) >= 3:
            self.rois.append(self.current_roi)
        
        self.current_roi = None
        self.update()

    def clear_rois(self):
        if len(self.rois) > 0:
            self.rois.pop()# = []
        self.current_roi = None
        self.update()

    
        
        
    def save_rois_to_json(self, exp_dir):
        data = {
            "rois":[]}
        for poly in self.rois:
            data["rois"].append({
                "id":poly.name,
                "points":[[p.x(), p.y()] for p in poly.points]})
        
        with open(SCRIPT_DIR / "rois.json", "w") as f:
            json.dump(data, f, indent=2)
      #  print(f"saved {len(self.rois)} ROIs to rois.json")
        
        with open(exp_dir / "rois.json", "w") as f:
            json.dump(data, f, indent=2)
    
    def load_rois_from_json(self):
        try:            
            with open(SCRIPT_DIR /"rois.json","r") as f:
                data = json.load(f)
            
            self.rois.clear()
            
            for roi in data["rois"]:
                current_roi = PolygonROI(roi["id"])
                poly = [QPointF(x,y) for x,y in roi["points"]]
                current_roi.points = poly
                self.rois.append(current_roi)

            """ if the above loop doesn´t work, check this one
            for roi in data["rois"]:
                new_roi = PolygonROI(roi["id"])
                new_roi.points = [QPointF(x,y) for x,y in roi["points"]]
                self.rois.append(new_roi)
                """
            
            self.update()
           # print(f"loaded {len(self.rois)} ROIs from rois.json")
        except FileNotFoundError:
            print("No stored ROIs found")
            
    def lock_rois(self):
        self.drawing_enabled = False
        self.editing_enabled = False

    def save_current_view(self, folder, filename):
        pixmap = self.grab()
        if pixmap.isNull():
            print("failed to grab image")
            return
        output = str(folder/filename)
        pixmap.save(output, "JPEG", quality=95)
        print("Saved ROI overlay")


class MainWindow(QMainWindow):

    frame_received = pyqtSignal(object)
    
    def __init__(self):
        super().__init__()
        
        #create root directory to save experiment files
   #     SCRIPT_DIR = Path(__file__).resolve().parent
   #     EXP_DIR = SCRIPT_DIR.parent
   #     self.experiments_root = EXP_DIR /"experiments"
        self.experiments_root = Path.home() / "Desktop" /"experiments"
        self.experiments_root.mkdir(exist_ok=True)
        
        self.setWindowTitle("LegM – v1.0.0")
        
        self.resolution = (1344,752)
        self.fps = 2
        self.sp_list = None
        self.dur_list = None
        self.rois = None
        self.g_thread = None
        
        # ---- Display widget ----
        self.image_widget = ImageDisplayWidget(self.resolution)

        self.frame_received.connect(self.image_widget.set_frame)
        
        self.graph_display = QLabel("Graph will appear here")
        self.graph_display.setMinimumSize(500,200)
        self.graph_display.setAlignment(Qt.AlignCenter)
        # layout 1st level
        layout = QHBoxLayout()
        #layout.addWidget(self.image_widget)
        
        # layout 2nd level
        controls_layout = QVBoxLayout()
        visual_layout = QVBoxLayout()
        visual_layout.addWidget(self.image_widget)
        visual_layout.addWidget(self.graph_display)
        
        # layout 3rd level
        setup_layout = QVBoxLayout()
        run_layout = QVBoxLayout()
        exit_layout = QVBoxLayout()
        
        # layout 4th level
                
        phases_conf_layout = QHBoxLayout()
        phases_label = QLabel("Number of phases:")
        
        
        self.phases_number_spin = QSpinBox()
        self.phases_number_spin.setRange(1,12)
        
        
        self.load_protocol_from_json()
        self.phases_number = len(self.sp_list)
        self.phases_number_spin.setValue(self.phases_number)
        phases_conf_layout.addWidget(phases_label)
        phases_conf_layout.addWidget(self.phases_number_spin)
        
        self.checkBox_temp = QCheckBox()
        self.checkBox_temp.setChecked(True)
        self.checkBox_temp.setText ("Enable temperature control")
        self.checkBox_temp.stateChanged.connect(self.disable_relay_main)
        
        
        duration_conf_layout = QHBoxLayout()
        duration_label = QLabel("Video duration [h]:")
        self.elapsed_label = QLabel("Elapsed time 00:00:00")
        self.current_phase_label = QLabel("Current phase:")
        self.checkBox_graph = QCheckBox()
        self.checkBox_graph.setChecked(True)
        self.checkBox_graph.setText ("post-experiment graph")
        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(0.1,72.0)
        self.duration_spin.setSingleStep(0.1)
        self.duration_spin.setValue(24)
        self.duration_spin.setDecimals(1)
        duration_conf_layout.addWidget(duration_label)
        duration_conf_layout.addWidget(self.duration_spin)
        
        self.protocol_group = QGroupBox("Temperature protocol")
        self.protocol_conf_layout = QGridLayout()
        
        self.phases_number_spin.valueChanged.connect(self.update_protocol_grid)
        
        self.sp_spins = []
        self.dur_spins = []
        
        self.update_protocol_grid()

        self.protocol_group.setLayout(self.protocol_conf_layout)

        self.setup_btn = QPushButton("Draw ROIs")
        self.use_stored_rois_btn = QPushButton("Use stored ROIs")
        self.clear_btn = QPushButton("Clear ROIs")
        self.run_btn = QPushButton("Run")
        self.stop_btn = QPushButton("Stop")
        self.exit_btn = QPushButton("EXIT")
        
        

       
        line_1 = QFrame()
        line_1.setFrameShape(QFrame.HLine)
        line_1.setFrameShadow(QFrame.Sunken)

        line_2 = QFrame()
        line_2.setFrameShape(QFrame.HLine)
        line_2.setFrameShadow(QFrame.Sunken)

        line_3 = QFrame()
        line_3.setFrameShape(QFrame.HLine)
        line_3.setFrameShadow(QFrame.Sunken)

        #add widgets to layouts
        setup_layout.addWidget(self.setup_btn)
        setup_layout.addWidget(self.use_stored_rois_btn)
        setup_layout.addWidget(self.clear_btn)
        setup_layout.addWidget(line_1)
 
        #run_layout.addWidget(duration_label)
        #run_layout.addWidget(self.duration_spin)
        run_layout.addWidget(self.checkBox_temp)
        run_layout.addLayout(phases_conf_layout)
        run_layout.addWidget(self.protocol_group)
        run_layout.addLayout(duration_conf_layout)
        run_layout.addWidget(self.checkBox_graph)
        run_layout.addWidget(self.elapsed_label)
        run_layout.addWidget(self.current_phase_label)
        run_layout.addWidget(self.run_btn)
        run_layout.addWidget(self.stop_btn)
        
        run_layout.addWidget(line_3)
        run_layout.addStretch()
        exit_layout.addWidget(line_2)
        exit_layout.addWidget(self.exit_btn)

        # organize nested layouts
        layout.addLayout(visual_layout)
        layout.addLayout(controls_layout)
        
        controls_layout.addLayout(setup_layout)
        controls_layout.addLayout(run_layout)
        controls_layout.addLayout(exit_layout)
        
        central = QWidget()
        self.setCentralWidget(central)
        central.setLayout(layout)

        self.setup_btn.clicked.connect(self.enter_setup_mode)
        self.run_btn.clicked.connect(self.enter_run_mode)
        self.stop_btn.clicked.connect(self.stop_experiment)
        self.clear_btn.clicked.connect(self.clear_rois_main)
        self.exit_btn.clicked.connect(self.close)
        self.use_stored_rois_btn.clicked.connect(self.use_stored_rois)

        self.stop_btn.setEnabled(False)

        # ------ Initialize temperature monitor
       # try:
        self.monitor = TemperatureMonitor()
        # Connect the signal directly to the widget's update method
        
        
        # ---Camera worker---
        self.camera_worker = CameraWorker(self.resolution, self.fps)
        self.camera_thread = QThread()
        self.camera_worker.moveToThread(self.camera_thread)
        
        if self.monitor:#.sensorFound:
            print("Temperature Monitor Initialized (Idle Mode)")
            self.monitor.status_updated.connect(self.update_ui_hardware_status)
            self.monitor.status_updated.connect(self.camera_worker.update_temp_value)
        
        if not self.monitor.sensorFound:
            self.protocol_group.setTitle("Running at RT")
            self.checkBox_temp.setEnabled(False)
            self.checkBox_temp.setChecked(False)
            self.current_phase_label.setEnabled(False)
            self.protocol_group.setEnabled(False)
            self.phases_number_spin.setEnabled(False)

#        except Exception as e:
            print(f"Could not initialize hardware monitor")# {e}")
            #self.monitor = None


        # ---- Analysis worker ----
        self.analysis_worker = AnalysisWorker(self.resolution)
        self.analysis_thread = QThread()
        self.analysis_worker.moveToThread(self.analysis_thread)
        self.analysis_worker.graph_ready.connect(self.update_graph_ui)
        
        
#         # ---Camera worker---
#         self.camera_worker = CameraWorker(self.resolution, self.fps)
#         self.camera_thread = QThread()
#         self.camera_worker.moveToThread(self.camera_thread)

        # start camera  when thread starts
        self.camera_thread.started.connect(self.camera_worker.start_camera)

        #When worker has a frame, send it to the widget and analysis
        #self.camera_worker.frame_for_gui.connect(self.image_widget.set_frame)
        self.camera_worker.frame_for_gui.connect(self.handle_camera_frame)
        self.camera_worker.frame_for_analysis.connect(self.analysis_worker.process_frame)
        self.camera_worker.elapsed_time_updated.connect(self.update_elapsed_ui)
        # If camera times out, call the same stop logic as the button
        self.camera_worker.finished_timeout.connect(self.stop_experiment)

        #Start the thread
        self.camera_thread.start()
        self.analysis_thread.start()
        
        #self.state = ExperimentState()
        self.state = ExperimentState.SETUP
        if self.monitor:
            status = self.monitor.get_status()
            self.update_ui_hardware_status(status["temperature"],status["setpoint"],status["heating"],status['sensor_found'],status['mode'],status['phase'])
     
    def disable_relay_main(self):
        if not self.checkBox_temp.isChecked():
            self.protocol_group.setTitle("Running at RT")
        else:
            self.protocol_group.setTitle("Current phase")
        self.current_phase_label.setEnabled(not self.current_phase_label.isEnabled())
        self.protocol_group.setEnabled(not self.protocol_group.isEnabled())
        self.phases_number_spin.setEnabled(not self.phases_number_spin.isEnabled())
        
        
    def update_protocol_grid(self):
        #clear everything in the grid
        while self.protocol_conf_layout.count():
            item = self.protocol_conf_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        
        #reset the lists
        self.sp_spins = []
        self.dur_spins = []
        
        #draw headers
        self.protocol_conf_layout.addWidget(QLabel("Phase"),0,0)
        self.protocol_conf_layout.addWidget(QLabel("Temp [°C]"), 0,1)
        self.protocol_conf_layout.addWidget(QLabel("Duration [h]"), 0,2)
        
        # draw the rows
        num_phases = self.phases_number_spin.value()
        
        for i in range(num_phases):
            
            self.protocol_conf_layout.addWidget(QLabel(f"{i+1}:"), i+1, 0)
            
            # Temp Spinbox
            sp_spin = QDoubleSpinBox()
            sp_spin.setRange(15.0, 35.0)
            sp_spin.setSingleStep(0.5)
            try:
                sp_spin.setValue(self.sp_list[i]) #sp_list is created at init from json file or default value
            except IndexError:
                sp_spin.setValue(25)
            #sp_spin.setSuffix(" °C")
            self.sp_spins.append(sp_spin)
            self.protocol_conf_layout.addWidget(sp_spin, i+1, 1)
            
            # Duration Spinbox
            dur_spin = QDoubleSpinBox()
            dur_spin.setRange(0.01, 72.0)
            dur_spin.setSingleStep(0.1)
            try:
                dur_spin.setValue(self.dur_list[i]) #dur_list is created at init from json file or default value
            except IndexError:
                dur_spin.setValue(5)
            #dur_spin.setSuffix(" h")
            self.dur_spins.append(dur_spin)
            self.protocol_conf_layout.addWidget(dur_spin, i+1, 2)
        
        self.adjustSize()


    @pyqtSlot(int)
    def update_elapsed_ui(self,ms):
        seconds = ms // 1000
        hours,remainder = divmod(seconds,3600)
        minutes,seconds = divmod(remainder,60)
        time_str = f"Elapsed time {hours:02d}:{minutes:02d}:{seconds:02d}"
        self.elapsed_label.setText(time_str)
                            
    @pyqtSlot(float, object, bool,bool,object,object)
    def update_ui_hardware_status(self, temp, sp, is_heating,sensor_found,mode,phase):
        """This runs whenever the sensor thread has new data"""
        self.image_widget.set_status(
                 temp=temp,
                 sp=sp,
                 heating=is_heating,
                 sensor_found=sensor_found,
                 mode=mode,
                 phase=phase)
        if isinstance(phase, (int,float)):
            phase_text = f"{phase +1}"
        else:
            phase_text = ""
        self.current_phase_label.setText(f"Current phase: {phase_text}")
        
   
    @pyqtSlot(QImage)
    def update_graph_ui(self, qimg):
        if qimg.isNull():
            return
        pixmap = QPixmap.fromImage(qimg)
        scaled_pixmap = pixmap.scaled(self.graph_display.size(), Qt.KeepAspectRatio,Qt.SmoothTransformation)
        self.graph_display.setPixmap(pixmap.scaled(self.graph_display.size(), Qt.KeepAspectRatio))
    

    @pyqtSlot(object)
    def handle_camera_frame(self,frame):
        """This receives frames from the Camera Thread safely"""
        self.image_widget.set_frame(frame)
            
    def enter_setup_mode(self):
  #      print("Entering SETUP mode")
        self.state = ExperimentState.SETUP
        self.analysis_worker.state = ExperimentState.SETUP
        self.image_widget.drawing_enabled = True
        self.duration_spin.setEnabled(True)

    def clear_rois_main(self):
        self.image_widget.clear_rois()
        
    def use_stored_rois(self):
#         print("Using stored ROIs")
        self.enter_setup_mode()
        self.image_widget.load_rois_from_json()
        #self.image_widget.lock_rois()
        #self.finalize_setup_and_start_run()
    
    def load_protocol_from_json(self):
        protocol_path = SCRIPT_DIR / "protocol.json"
        try:            
            with open(protocol_path,"r") as f:
                data = json.load(f)
            #----------------------------
                #en init crear una lista de sp y dur vacia, luego ejecutar esta funcion y si no hay archivo, cargar los datos por defecto
            self.sp_list = data["SP"]
            self.dur_list = data["duration"]
            
        except FileNotFoundError:
            self.sp_list = [30.0,23.0]
            self.dur_list = [5.0, 15.0]
            print("No protocol found, using default values")
            
    def save_protocol_to_json(self,sp,dur):
        protocol_path = SCRIPT_DIR / "protocol.json"
        data = {"SP":sp, "duration":dur}
        with open(protocol_path, "w") as f:
            json.dump(data, f, indent=2)
       # print(f"saved temperature protocol to protocol.json")
        
    def enter_run_mode(self):
        default_name = default_experiment_name()
        name, ok = QInputDialog.getText(self,
                                        "Experiment",
                                        "Name: ",
                                        text=default_name)
        if not ok:
            print("Experiment setup cancelled")
            return
        
        self.experiment_name = name.strip()
        self.experiment_dir = self.experiments_root / self.experiment_name
        self.experiment_dir.mkdir(exist_ok=True)

        if self.monitor.sensorFound and self.checkBox_temp.isChecked():
            sp_list = [spin.value() for spin in self.sp_spins]
            dur_list = [spin.value() for spin in self.dur_spins]
            self.save_protocol_to_json(sp_list,dur_list)
            self.monitor.set_protocol(sp_list, dur_list)
            
        else:
            self.monitor.set_mode(self.checkBox_temp.isChecked())
            print("Running at RT")

        
        csv_path = self.experiment_dir / f"{self.experiment_name}.csv"
        self.analysis_worker.setup_experiment(self.image_widget.rois, str(csv_path), self.monitor)

        self.state = ExperimentState.RUN
        self.analysis_worker.state = ExperimentState.RUN
       # self.camera_worker.start_experiment(output_dir = self.experiment_dir, experiment_name=self.experiment_name, duration_s=self.duration_s)


        
       # print("Entering RUN mode")
        self.image_widget.lock_rois()
        if len(self.image_widget.rois) > 0:
            self.image_widget.save_rois_to_json(self.experiment_dir)
        self.image_widget.save_current_view(self.experiment_dir, self.experiment_name)
        duration_s = int(self.duration_spin.value() * 3600)

            # 2. Setup Analysis
        
        self.duration_spin.setEnabled(False)
        self.run_btn.setEnabled(False)
        self.setup_btn.setEnabled(False)
        self.use_stored_rois_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.clear_btn.setEnabled(False)
        self.protocol_group.setEnabled(False)
        self.phases_number_spin.setEnabled(False)
        self.exit_btn.setEnabled(False)
        self.checkBox_temp.setEnabled(False)
        self.experiment_running = True

        self.camera_worker.start_experiment(self.experiment_dir, name, duration_s)
        

    def stop_experiment(self):
        if self.state != ExperimentState.RUN:
            return
        
        self.stop_btn.setEnabled(False)
        self.camera_worker.stop_experiment()
        if self.monitor:
            self.monitor.stop()
        
        if self.checkBox_graph.isChecked() and len(self.image_widget.rois) > 0:
            # Update status so the user knows it's processing
            self.elapsed_label.setText("Generating Final Graph... Please wait.")

            self.g_thread = QThread()
            self.g_worker = GraphWorker(str(self.experiment_dir), self.experiment_name)
            self.g_worker.moveToThread(self.g_thread)

            # Connections
            self.g_thread.started.connect(self.g_worker.run)
            self.g_worker.finished.connect(self.g_thread.quit)
            self.g_worker.finished.connect(self.g_worker.deleteLater)
            #self.g_thread.finished.connect(self.g_thread.deleteLater)
            self.g_worker.finished.connect(self.on_graphing_finished)
            
            self.g_thread.start()
        else:
            self.on_graphing_finished()

    def on_graphing_finished(self):
        
        # 6. restore GUI state
        self.run_btn.setEnabled(True)
        self.clear_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.setup_btn.setEnabled(True)
        self.use_stored_rois_btn.setEnabled(True)
        self.duration_spin.setEnabled(True)
        self.image_widget.drawing_enabled = True
        self.exit_btn.setEnabled(True)
        if self.monitor.sensorFound:
            self.protocol_group.setEnabled(True)
            self.phases_number_spin.setEnabled(True)
            self.checkBox_temp.setEnabled(True)
        self.elapsed_label.setText("Elapsed time 00:00:00")
        #self.current_phase_label.setText("Current phase:")
        if hasattr(self,"g_thread") and self.g_thread is not None:
            self.g_thread.wait()
            self.g_thread = None
        #self.g_worker = None
            
        self.state = ExperimentState.SETUP
        self.analysis_worker.state = ExperimentState.SETUP
        
    def closeEvent(self, event):
        if hasattr(self,"g_thread") and self.g_thread is not None:
            if self.g_thread.isRunning():
                self.elapsed_label.setText("Closing...waiting for graph")
                self.g_thread.quit()
                if not self.g_thread.wait(5000):
                    print("Graphing thread timed out, forcing exit")
            self.g_thread = None
            
        if self.monitor:
            self.monitor.stop()
            self.monitor.close()
            
        self.camera_worker.stop_experiment()
        
        if self.camera_thread.isRunning():
            self.camera_thread.quit()
            self.camera_thread.wait()
        
        if self.analysis_thread.isRunning():
            self.analysis_thread.quit()
            self.analysis_thread.wait()
            
        event.accept()






class CameraWorker(QObject):
    """
controls experiment-related camera actions:
- video recording
-frame forwarding to analysis
the camera itself is owned and started by mainwindow.

"""
    frame_for_analysis = pyqtSignal(object)
    frame_for_gui = pyqtSignal(object)
    finished_timeout = pyqtSignal()
    elapsed_time_updated = pyqtSignal(int)
   
    def __init__(self, resolution, fps,parent=None):
        super().__init__(parent)
        
        self.current_temp = "no sensor"
        self.size = (210,40)
        self.fps = fps
        self.frame_dur_limits = int(1000000/self.fps)
        self.resolution = resolution
        self.picam2 = None
 
        self.video_encoder = None
        self.video_output = None


        #Duration control
        self.duration_s = 0
        self.timer = QElapsedTimer()
        self.experiment_running = False        #internal flag for recording logic
        self.frame_count = 0
       
         
    @pyqtSlot(float, object, bool,bool,object,object)
    def update_temp_value(self, temp, sp, is_heating,sensor_found,mode,phase):
        if sensor_found:
            self.current_temp = f"{temp:.1f}"
            self.size = (100,40)
        else:
            self.current_temp = "no sensor"
            self.size = (210,40)
        
    @pyqtSlot()
    def start_camera(self):
        
        """Initialize hardware inside the worker"""

        self.picam2 = Picamera2()  
        config = self.picam2.create_video_configuration(main={"format": "RGB888","size": self.resolution},controls ={"FrameDurationLimits":(self.frame_dur_limits,self.frame_dur_limits)},transform = Transform(hflip=True, vflip=True),buffer_count = 6)
#             """        #| Mode        | Integer |
# | ----------- | ------- |
# | Auto        | **0**   |
# | Tungsten    | **1**   |
# | Fluorescent | **2**   |
# | Indoor      | **3**   |
# | Daylight    | **4**   |
# | Cloudy      | **5**   |
# | Custom      | **6**   |
# """
        
##        self.experiment_name = None
##        self.experiment_dir = None
       
        self.picam2.align_configuration(config)
        self.picam2.configure(config)
        self.picam2.set_controls({"AwbEnable":False})
        self.picam2.set_controls({"ColourGains":(1,1)})

        # set the callback to a local method
        self.picam2.pre_callback = self._draw_temp
        self.picam2.post_callback = self._on_hardware_frame

        self.picam2.start()
     #   self.picam2.video_configuration.controls.FrameRate = self.fps

    def _draw_temp(self,request):
        text = f"{self.current_temp}" 
        with MappedArray(request, "main") as m:
            cv2.rectangle(m.array,(0,0),self.size,(0,0,0),-1)
            cv2.putText(m.array, text,(10,30), cv2.FONT_HERSHEY_SIMPLEX,1.2,(255,255,255),2)#,cv2.LINE_AA)
            
    def _on_hardware_frame(self,request):
        """Hardware callback - Run s in a non-GUI Thread"""
        frame = request.make_array("main")   #see if send all frames to gui or just 1Hz
        frame_to_submit = frame.copy()
        self.frame_for_gui.emit(frame_to_submit)

        if self.experiment_running:
            current_ms = self.timer.elapsed()
            if current_ms >= (self.duration_s * 1000):
                self.finished_timeout.emit() # Tell UI to stop
                
            else:
                self.frame_count += 1
                if self.frame_count >= self.fps:
                    self.elapsed_time_updated.emit(current_ms)
                    #frame = request.make_array("main")   #see if send all frames to gui or just 1Hz
                    #frame_to_submit = frame.copy()
                    self.frame_count = 0
                    self.frame_for_analysis.emit(frame_to_submit) 
                
                                         

                
    def start_experiment(self, output_dir, experiment_name, duration_s):
        """Called by MainWindow when RUN is pressed
"""
        if self.experiment_running:
            pass
        
        self.experiment_running = True
        self.duration_s = duration_s
        
     #   video_path = os.path.join(output_dir,f"{experiment_name}.h264")
        video_path = output_dir / f"{experiment_name}.h264"
        self.video_encoder = H264Encoder(bitrate=1000000)
        #print(f"videopath {video_path}")
        self.video_output = FileOutput(str(video_path))

        self.picam2.start_recording(self.video_encoder, self.video_output)
        self.timer.start()
        print("Recording started")


        
    def stop_experiment(self):
        """Called manually or by timeout"""
        
        if not self.experiment_running:
            return

        self.picam2.stop_encoder()
        #self.picam2.stop_recording()
        self.video_encoder = None
        self.video_output = None
        self.experiment_running = False     
        print("Experiment stopped")
    
    


        
        
        
if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())
