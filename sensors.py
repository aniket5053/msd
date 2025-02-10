import time
import board
import adafruit_sht4x
from collections import deque

class SensorManager:
    def __init__(self, max_history=100):
        self.sht = adafruit_sht4x.SHT4x(board.I2C())
        self.history = deque(maxlen=max_history)
        
    def read_sensors(self):
        try:
            temperature = round(self.sht.temperature, 2)
            humidity = round(self.sht.relative_humidity, 2)
            timestamp = time.time() * 1000  # JS timestamp
            self.history.append({
                'timestamp': timestamp,
                'temperature': temperature,
                'humidity': humidity
            })
            return temperature, humidity
        except Exception as e:
            print(f"Sensor read error: {e}")
            return None, None

    def get_history(self):
        return list(self.history)