# -*- coding: utf-8 -*-
"""
Created on Thu Jul 10 20:56:38 2025

@author: Lorenzo F.P. Merlo

@alias Makiavel
"""
# import sys
import asyncio
import nest_asyncio
import logging
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('ibkr_connection.log'),
        logging.StreamHandler()
    ])

logger = logging.getLogger(__name__)

#%% Import libraries 
from ib_insync import IB, util, Option, Stock, Bond
import pandas as pd
import numpy as np
import time
import datetime as dt
#%%the class works as a module where I can call each of the functions using the prefix ibkr like this: 
#ibkr = IBKRConnector(port=7497)
#ibkr.connect()
#ibkr.account_summary
#%% START CLASS
class IBKRConnector:
    #host, port, and client_id are parameters used to configure the connection to TWS (port 7497) or IB Gateway (4001) and __init__ method does not yet connect to the IB API
    def __init__(self, host='127.0.0.1', port=4001, client_id=1):
        """
        Initialize the connector but do not connect yet.
        """
        self.host = host
        self.port = port
        self.client_id = client_id
        self.ib = IB()
        self.connected = False
        logger.info(f"Initialized IBKRConnector with host={self.host}, port={self.port}, client_id={self.client_id}")

    #connect to the IB API with the configuration set above => in the master scripts (calling this class (module) and functions with import IBKRConnector as ibkr)
    def connect(self):
     """
     Connect to IBKR TWS or Gateway.
     """
     if self.connected:
        logger.warning("⚠️ Already connected.")
        return

     try:
        # ✅ Start event loop safely here
        try:
            util.startLoop()
        except RuntimeError:
            logger.debug("Event loop already running")

        self.ib.connect(self.host, self.port, self.client_id)
        self.connected = True
        logger.info(f"✅ Connected to IBKR at {self.host}:{self.port} with client ID {self.client_id}")
     except Exception as e:
        logger.error(f"❌ Connection failed: {e}")
        raise

    #disconnect from the IB API at 
    def disconnect(self):
        """
        Disconnect from API.
        """
        if self.connected:
            self.ib.disconnect()
            self.connected = False
            logger.info("🔌 Disconnected from IBKR API")
        else:
            logger.warning("⚠️ No active connection to disconnect.")

    def is_connected(self):
       return self.ib.isConnected()
    
    def ib_instance(self):
       return self.ib
    
    # Sleep method (useful to pause for async data gathering)
    def sleep(self, seconds):
        self.ib.sleep(seconds)

    # Run the event loop once (useful for async)
    def run_once(self):
        self.ib.waitOnUpdate(timeout=0)
