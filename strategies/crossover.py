import pandas as pd
import pandas_ta as ta
import numpy as np
from binance import ThreadedWebsocketManager
import time

class Trader():
      def __init__(self, client, symbol, bar_length, start, stop_loss, take_profit, units):
            self.client = client
            self.symbol = symbol
            self.available_intervals = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"]
            self.bar_length = bar_length
            self.start = start
            self.stop_loss = stop_loss
            self.take_profit = take_profit
            self.units = units
            self.fast_length = 12
            self.slow_length = 26
            self.signal_length = 9
            self.rsi_length = 14
            self.in_position = False

        
      def start_trading(self):
            self.twm = ThreadedWebsocketManager()
            self.twm.start()

            if self.bar_length in self.available_intervals:
                 self.get_recent(symbol = self.symbol, interval = self.bar_length, start = self.start)
                 self.twm.start_kline_socket(callback = self.stream_candles,
                                        symbol = self.symbol, interval = self.bar_length)
            
            self.calculate_indicator()
            self.execute_trades()

      # Get Historical data
      def get_recent(self, symbol, interval, start):
       
        df = pd.DataFrame(self.client.get_historical_klines(symbol=symbol, interval=interval, start_str=start))
        df["Date"] = pd.to_datetime(df.iloc[:,0], unit = "ms")
        df.columns = ["Open Time", "Open", "High", "Low", "Close", "Volume",
                      "Clos Time", "Quote Asset Volume", "Number of Trades",
                      "Taker Buy Base Asset Volume", "Taker Buy Quote Asset Volume", "Ignore", "Date"]
        df = df[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()
        df.set_index("Date", inplace = True)
        df[['High', 'Low', 'Close', 'Volume']] = df[['High', 'Low', 'Close', 'Volume']].astype(float)
        for column in df.columns:
                df[column] = pd.to_numeric(df[column], errors = "coerce")
        df["Complete"] = [True for row in range(len(df)-1)] + [False]
        self.data = df
        
     
    
      # Generate all trading's indicators
      def calculate_indicator(self):
        # Calcul des EMAs
        self.data['EMA_Short'] = ta.ema(self.data["Close"], 15)
        self.data['EMA_Long'] = ta.ema(self.data["Close"], 30)

        # Calculate MACD
        macd = ta.macd(self.data["Close"], fast=self.fast_length, slow=self.slow_length, signal=self.signal_length)
        self.data['MACD'] = macd['MACD_12_26_9']
        self.data['MACD_Histogram'] = macd['MACDh_12_26_9']
        self.data['MACD_Signal'] = macd['MACDs_12_26_9']
        self.data['RSI'] = ta.rsi(self.data["Close"], length=self.rsi_length)
        self.generate_crossover_signal()
        self.generate_macd_signal()
        self.generate_entry_signal()
      
      
      # Generate CROSSOVER signal
      def generate_crossover_signal(self):
        self.data["EMA_Trend"] = 0.0
        self.data["EMA_Trend"] = np.where(self.data["EMA_Short"] > self.data["EMA_Long"], 1.0, 0.0)
        self.data["Crossover_Trend"] = self.data["EMA_Trend"].diff()
      
      # Generate MACD signal
      def generate_macd_signal(self):
         macd_bullish = (self.data['MACD'] > self.data['MACD_Signal'])
         macd_bearish = (self.data['MACD'] < self.data['MACD_Signal'])

         self.data["MACD_Trend"] = np.where(macd_bullish, 1, np.where(macd_bearish, -1, 0))
      
      def stream_candles(self, msg):
        event_time = pd.to_datetime(msg["E"], unit = "ms")
        start_time = pd.to_datetime(msg["k"]["t"], unit = "ms")
        first   = float(msg["k"]["o"])
        high    = float(msg["k"]["h"])
        low     = float(msg["k"]["l"])
        close   = float(msg["k"]["c"])
        volume  = float(msg["k"]["v"])
        complete = msg["k"]["x"]
        print("Time: {} | Price: {}".format(event_time, close))
        new_data = pd.DataFrame({
        "Open": [first],
        "High": [high],
        "Low": [low],
        "Close": [close],
        "Volume": [volume],
        "Complete": [complete]
        }, index=[start_time])

        # Check if the candle is complete
        if complete:
            if start_time in self.data.index:
                # Update candle
                self.data.loc[start_time] = new_data.loc[start_time]
            else:
                # Concat with existing data
                self.data = pd.concat([self.data, new_data])

      # Combine CROSSOVER signal with MACD signal for generate a entry signal
      def generate_entry_signal(self):
         buy_signal = (self.data["Crossover_Trend"] > 0) & (self.data["MACD_Trend"] == 1) # Buy signal
         sell_signal = (self.data["Crossover_Trend"] < 0) & (self.data["MACD_Trend"] == -1) # Sell signal

         self.data["Entry"] = np.where(buy_signal, 1, np.where(sell_signal, -1, 0))
       

      def execute_trades(self):
        entry = self.data["Entry"].iloc[-1]
        last_close = self.data["Close"].iloc[-1]
        last_rsi = self.data["RSI"].iloc[-1]
        self.last_entry_price = None

        try:
            if entry == 1 and last_rsi < 65 and self.in_position == False:
                self.last_entry_price = last_close
                order = self.client.create_order(symbol=self.symbol, side="BUY", type="MARKET", quantity=self.units)
                print(f"Achat effectué : {order} ")
                self.in_position = True
            elif entry == -1 and last_rsi > 35 and self.in_position == True:
                self.last_entry_price = last_close
                order = self.client.create_order(symbol=self.symbol, side="SELL", type="MARKET", quantity=self.units)
                print(f"Vente effectuée : {order}")
                self.in_position = False
            else:
                print(f"Aucune transaction effectué")
            
            if self.in_position:
                stop_loss_price = self.last_entry_price * (1 - self.stop_loss / 100)
                target_profit_price = self.last_entry_price * (1 + self.target_profit / 100)

                if last_close <= stop_loss_price or last_close >= target_profit_price:
                    # Close position
                    order = self.client.create_order(symbol=self.symbol, side="SELL", type="MARKET", quantity=self.units)
                    print(f"Position clôturée à {last_close}")
                    self.in_position = False
                    self.last_entry_price = None
        except Exception as e:
                print(f"Erreur: {e}")

def run_crossover_strategy(client, symbol, bar_length, start, stop_loss, take_profit, units):
    trader = Trader(client, symbol, bar_length, start, stop_loss, take_profit, units)
    trader.start_trading()


    try:
        while True:       
            # Break between iterations
            time.sleep(60)

    except KeyboardInterrupt:
        print("Stratégie interrompue par l'utilisateur")

    finally:
        trader.twm.stop()
        
    return trader.data[trader.data["Entry"] != 0]
