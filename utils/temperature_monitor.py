import threading
import time
from w1thermsensor import W1ThermSensor
from w1thermsensor.errors import SensorNotReadyError
#install with sudo apt-get install python3-w1thermsensor

from w1thermsensor.errors import NoSensorFoundError
import RPi.GPIO as GPIO
from PyQt5.QtCore import QObject, pyqtSignal

# Keep your constants
RELAY_PIN = 5 #(Physical pin 29)
GPIO_MODE = GPIO.BCM
TEMPERATURE_READ_INTERVAL = 1.0
WARMUP_DELAY = 2  # Reduced for faster UI feedback
TEMPERATURE_HYSTERESIS = 0.0
MIN_VALID_TEMPERATURE = 15.0
MAX_VALID_TEMPERATURE = 35.0
MODE_HEAT = "heat"
MODE_COOL = "cool"
MODE_RT = "RT"
MODE_NO_SENSOR = "no_sensor"

class TemperatureMonitor(QObject):
    # Signal to notify the GUI whenever a new temperature is read
    # Format: (current_temp, current_setpoint, is_heating)
    status_updated = pyqtSignal(float, object, bool,bool,object,object)

    def __init__(self, SP_list=None, duration_list=None, hysteresis=TEMPERATURE_HYSTERESIS):
        super().__init__()

        #Initialize sensor
        self.sensor = None
        self.temperature = 25.0 # Default fallback
        self.sensorFound = False
        
        # Hardware setup
        self.relayPin = RELAY_PIN
        self._initialize_sensor()
        if self.sensorFound:
            self._initialize_gpio()
            self.measuring = True
        else:
            self.measuring = False
        
        self.hysteresis = hysteresis
        self.heating = False
        self.mode = None #MODE_HEAT
        self.setPoint = None #25 #23.0 # Default setup temperature
        
        # Cycling control
        self.cycling_enabled = False
        self.cycling_start = False
        self.SP_list = []
        self.duration_list = []
        self.cycle_index = None
        self.window_start = None
        self.current_window = None

#         if SP_list and duration_list:
#             self.set_protocol(SP_list, duration_list)

        # Thread control
        self.start_time = time.time()
        self.last_reading_time = time.time()
        self.running = False
        
        
        #if self.sensorFound:
        self.thread = threading.Thread(target=self._update_loop, daemon=True)
        self.thread.start()

    def set_protocol(self, SP_list, duration_list):
        """Update the cycling parameters (Call this when 'Run' is pressed)"""
        if not SP_list or not duration_list:
            return
        self._validate_cycling_lists(SP_list, duration_list)
        self.SP_list = SP_list
        self.duration_list = [d * 3600 for d in duration_list] # Hours to Sec
        self.cycle_index = 0
        self.setPoint = self.SP_list[0]
        self.current_window = self.duration_list[0]
        self.cycling_enabled = True
        self.cycling_start = False # Will start once setpoint is reached
        
        # Determine initial mode
        #self.mode = MODE_HEAT if self.SP_list[0] > self.temperature else MODE_COOL
        self.mode = MODE_HEAT if self.SP_list[0] > 25 else MODE_COOL
        self.heating = True if self.mode == MODE_HEAT else False
        
        self.running = True
        self.window_start = None
        print(f"Protocol updated: Start at {self.setPoint}C")

    def _initialize_sensor(self):
        try:
            self.sensor = W1ThermSensor()
            self.sensorFound = True
            trial = 0
            while trial<3:
                raw_temp = self.sensor.get_temperature()
                if raw_temp is not None and self._is_valid_temperature(raw_temp):
                    self.temperature = raw_temp
                    break
                trial += 1
            print("DS18B20 Sensor OK")
        except Exception:
            print("Warning: Sensor not found.")
            self.temperature = 25
            self.sensorFound = False

    def _initialize_gpio(self):
        print("sensor initialization")
       # GPIO.setwarnings(False)
       # GPIO.cleanup()
        GPIO.setmode(GPIO_MODE)
        GPIO.setup(RELAY_PIN, GPIO.OUT, initial=GPIO.LOW)
        GPIO.output(RELAY_PIN, GPIO.LOW)
        
    def _update_loop(self):
        """Background thread for hardware control"""
        while self.measuring:
            now = time.time()
            if now - self.last_reading_time >= TEMPERATURE_READ_INTERVAL:
                self.last_reading_time = now
                try:
                    raw_temperature = self.sensor.get_temperature()
                    self.sensorFound = True
                    # Validate temperature reading
                    if self._is_valid_temperature(raw_temperature):
                        self.temperature = raw_temperature
                    else:
                        # Invalid reading - use setpoint as fallback
                        print(f"Warning: Sensor reading out of range ({raw_temperature}°C). Using setpoint.")
                        self.temperature = self.setPoint #if self.setPoint else raw_temperature
                    
                    if self.running:
                        self._control_relay()
                    # EMIT SIGNAL TO GUI
                  
                    
                    self.status_updated.emit(self.temperature, self.setPoint, self.heating,self.sensorFound, self.mode, self.cycle_index)
                except SensorNotReadyError:
                    self.sensorFound = False
                    self.status_updated.emit(self.temperature, self.setPoint, self.heating,self.sensorFound, self.mode, self.cycle_index)
                    GPIO.output(self.relayPin, GPIO.LOW)
                    print("sensor disconnected, relay OFF...")
                except Exception as e:
                    print(f"Sensor error: {e}")
            time.sleep(0.1)

    def _control_relay(self):
        
        """
        Control the relay based on current temperature and setpoint.
        Uses mode (heat/cool) to determine when to turn heater on/off.
        Implements hysteresis to prevent rapid switching.
        Only activates control after WARMUP_DELAY seconds from initialization.
        """
        # Don't control if no setpoint is configured or still in warmup period
        if self.setPoint is None or not self.sensorFound:
            return

        # Warmup check
        if time.time() - self.start_time <= WARMUP_DELAY:
            return
        # Control relay based on mode
        if self.mode == MODE_HEAT:
              # HEATING MODE: Turn on relay when below setpoint
            if self.temperature < self.setPoint and self.heating:
                GPIO.output(self.relayPin, GPIO.HIGH)  # Turn on heating
            else:
                # Temperature reached setpoint - turn off heating
                GPIO.output(self.relayPin, GPIO.LOW)  # Turn off heating
                self.heating = False
                
                # Start cycling timer if not already started
                if self.cycling_enabled and not self.cycling_start:
                    self.cycling_start = True
                    self.window_start = time.time()
                    print(f"Temperature cycling started: {self.setPoint}°C")
            if self.temperature < self.setPoint -self.hysteresis and not self.heating:
                self.heating = True

        elif self.mode == MODE_COOL:
            # COOLING MODE: Turn off relay, let temperature drop naturally
            GPIO.output(self.relayPin, GPIO.LOW)  # Keep heating off
            self.heating = False
            
            # Start cycling timer when we enter cooling mode
            if self.cycling_enabled and not self.cycling_start:
                self.cycling_start = True
                self.window_start = time.time()
                print(f"Temperature cycling started: {self.setPoint}°C (cooling)")

        if self.cycling_enabled and self.cycling_start:
            self._check_temperature_cycling()

    def _check_temperature_cycling(self):
        """
        Check if it's time to advance to the next setpoint in the cycling sequence.
        Loops back to the beginning when the sequence completes.
        """
        if not self.cycling_start or not self.cycling_enabled:
            return
        
        if self.current_window is None:
            return
        
        current_time = time.time()
        elapsed_time = current_time - self.window_start
        
        # Check if current phase duration has been reached
        if elapsed_time >= self.current_window:
            print(f"Elapsed time: {int(elapsed_time)}")
            # Move to next setpoint in the cycle
            self._advance_cycle()

    def _advance_cycle(self):

        # Move to next index (loop back to 0 if at end)
        self.cycle_index = (self.cycle_index + 1) % len(self.SP_list)
        
        old_sp = self.setPoint
        self.setPoint = self.SP_list[self.cycle_index]
        self.current_window = self.duration_list[self.cycle_index]
        
        self.cycling_start = False # Reset window
        #self.mode = MODE_HEAT if self.setPoint > old_sp else MODE_COOL if self.setPoint < old_sp else self.mode
        self.mode = MODE_HEAT if self.setPoint > 25 else MODE_COOL 
        self.heating = True if self.mode == MODE_HEAT else False
        print(f"Cycle advanced to: {self.setPoint}C")
        self.cycling_start = False  # Reset to wait for temperature to reach setpoint
        self.window_start = None



    def _validate_cycling_lists(self, SP_list, duration_list):
        """
        Validate that SP_list and duration_list are properly formatted.
        
        Args:
            SP_list (list): List of setpoints
            duration_list (list): List of durations in hours
            
        Raises:
            ValueError: If lists are invalid
        """
        if not isinstance(SP_list, list) or not isinstance(duration_list, list):
            raise ValueError("SP_list and duration_list must be lists")
        
        if len(SP_list) == 0 or len(duration_list) == 0:
            raise ValueError("SP_list and duration_list cannot be empty")
        
        if len(SP_list) != len(duration_list):
            raise ValueError(f"SP_list and duration_list must have same length "
                           f"(got {len(SP_list)} and {len(duration_list)})")
        
        if any(d <= 0 for d in duration_list):
            raise ValueError("All durations must be positive")
        
        if any(not isinstance(sp, (int, float)) for sp in SP_list):
            raise ValueError("All setpoints must be numbers")
        
    def _is_valid_temperature(self, temperature):
        """
        Validate that temperature reading is within acceptable range.
        
        Args:
            temperature (float): Temperature reading to validate
            
        Returns:
            bool: True if temperature is valid, False otherwise
        """
        return MIN_VALID_TEMPERATURE <= temperature <= MAX_VALID_TEMPERATURE

    
    def get_status(self):
        """Safe polling method for setup phases"""
        return {
            'temperature': self.temperature,
            'setpoint': self.setPoint,
            'heating': self.heating,
            'sensor_found': self.sensorFound,
            'mode': self.mode,
            'phase':self.cycle_index
        }

    def set_mode(self,control_enabled):
        if self.sensorFound:
            if not control_enabled:
                self.mode = MODE_RT
        else:
            self.mode = MODE_NO_SENSOR
        
    def stop(self):
        self.running = False
        self.setPoint = None
        self.cycle_index = None
        if self.sensorFound:
            GPIO.output(self.relayPin, GPIO.LOW)
            self.heating = False
            #GPIO.cleanup()
    
    def close(self):
        GPIO.cleanup()
