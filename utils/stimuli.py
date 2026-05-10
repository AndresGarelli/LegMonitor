#import cv2
#import numpy as np
##from random import randint
#import os
#import time
#import pandas as pd
##from collections import OrderedDict
#import csv
#import json


class TempRange():   
                
    def __init__(self, lower_limit = 18, upper_limit = 28.5):
        self.lower_limit = lower_limit
        self.upper_limit = upper_limit

    def set_ranges(self, temp_data, fin):
        inicioLow = []
        inicioHigh = []
        finLow = []
        finHigh = []
        prevState = None
        for idx,t in enumerate(temp_data):
            if t <= self.lower_limit:
                state = "a"
            elif t >= self.upper_limit:
                state = "c"
            else:
                state = "b"

            if prevState != state:
                if state == "a":
                    inicioLow.append(idx)
                elif state == "c":
                    inicioHigh.append(idx)
                else:
                    if prevState == "c":
                        finHigh.append(idx)
                    elif prevState == "a":
                        finLow.append(idx)
            prevState = state
        if len(finLow) < len(inicioLow):
            finLow.append(fin)
        if len(finHigh) < len(inicioHigh):
            finHigh.append(fin)

        limitsLow = zip(inicioLow,finLow)
        limitsHigh = zip(inicioHigh,finHigh)

        return limitsLow, limitsHigh
    

if __name__ == "__main__":
    TempRange()
