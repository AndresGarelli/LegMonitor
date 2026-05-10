#! /usr/bin/env python3


import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from utils.stimuli import TempRange
from io import BytesIO
import logging

logging.basicConfig(level=logging.CRITICAL)

class Graph_1h():
    def __init__(self, nROIs,roi_names, monitor):
        self.monitor = monitor
        self.counter = 0
        self.filter_value = 1
        self.suptitle = f'Motion events (last 60 min) - Filter {self.filter_value} %'
        #creates a list of numpy arrays that will hold the indexes of events
        self.data = [np.array([1800+roi]) for roi in range(nROIs)] #creates a 1D array with one value per ROI
        #self.data = [np.array([], dtype=np.int32) for roi in range(nROIs)]
        self.colors = [f'C{i}' for i in range(nROIs)]
        self.headers = [f'ROI{i}' for i in range(nROIs)]

        
        x = np.linspace(0, 2 * np.pi * 10, 3600)  # 10 full cycles over 3600 points
        # Create sine wave that oscillates between -1 and 1
        sine_wave = np.sin(x)
        # Rescale to range [17, 30]
        min_temp,max_temp = 17,30
        amplitude = (max_temp - min_temp) / 2
        offset = (max_temp + min_temp) / 2
        oscillating_values = amplitude * sine_wave + offset
        # Reshape to (1, 3600)
        #self.temp = oscillating_values.reshape((1, 3600))
        self.temp_buffer = oscillating_values.tolist()
        

    def update(self, currentimage):  #currentimage is a list with totalFrames, temperature, rois detection.
        roi_data = currentimage[5:]
        self.temp_buffer = self.temp_buffer[1:]
        self.temp_buffer.append(currentimage[1]) #slices from 2nd element of the array and appends one value in the axia=1 (columns)
        for i,roi in enumerate(roi_data):
            self.data[i] = self.data[i]-1  #subtract 1 to all existing values of the array
            self.data[i] = self.data[i][self.data[i]>=0] #remove those that are <0. If <0, it is an event that occured more than an hour ago and should not be inthe graph anymore
           # self.data[i] = self.data[i].reshape(-1,1)
            if roi > self.filter_value:
                self.data[i] = np.append(self.data[i], [3599])#, axis=1)
        self.counter += 1
        if self.counter%10 == 0:
            logging.debug("llama a makegraph")
            self.counter = 0
            return self.makeGraph()
        return None
        
    def makeGraph(self):
        logging.debug("adentro de makegraph")
        
        boundaries = TempRange()
        limitsLow, limitsHigh = boundaries.set_ranges(self.temp_buffer,3599)
        
        fig, ax = plt.subplots(figsize=(9,4))
        for x,y in limitsLow:
            plt.axvspan(x,y,0.05,0.95,color="lightblue") 
        for x,y in limitsHigh:
            plt.axvspan(x,y,0.05,0.95,color="papayawhip")                
        
        offset = 4
        ax.eventplot(self.data, colors=self.colors, linelengths=3, lineoffsets=offset, linewidths = 1)
        fig.suptitle(self.suptitle, fontsize=16)
        #positions = []
        #for i in range(len(headers)):
        #    positions. append(i * offset)
        #plt.yticks(positions, headers)  # Set the positions where the labels will appear

        positions = []   ##calcula donde poner las etiquetas del ejeY 
        for i in range(len(self.data)):
            positions. append(i * offset)
        ax.set_yticks(positions, self.headers)

        labels = [60,50,40,30,20,10]
        ticks =[0,600,1200,1800,2400,3000]
        ax.set_xticks(ticks,labels)
        ax.set_xlim(left= 0, right=3600)
        #plt.legend(headers, loc = "upper left", ncol = len(headers))
        plt.tight_layout()
        buf = BytesIO()
        plt.savefig(buf, format = "png")
        fig.clear()
        plt.close(fig)
        buf.seek(0)
        graph = buf.read()
        buf.close()
        return graph #buf.read()

# 
# if __name__ == "__main__":
#     basePath = str(Path().absolute())
#     outputFile =  "intensityValues"
#     cesar(basePath,outputFile)
# 
