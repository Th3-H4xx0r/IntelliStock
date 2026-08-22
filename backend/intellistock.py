import requests
import logging
from colorama import init 
from termcolor import colored 
import datetime as dt
import time
import calendar
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
import sqlite3
import uuid

from db import store

init()

# NOTE (Postgres port, G11): this module has ZERO importers -- it is the
# original 2021-era API shim, kept for reference. Its tables ("Prices",
# "Transactions") are not in db/schema.py's registry, so they would be
# created from the default template on first use. ReQL generated a UUID
# primary key for a document with no "id"; store.insert requires one, so the
# two insert sites below now generate the same thing explicitly.

def _epoch_to_iso(epoch_seconds):
    """R24 replacement for ``r.epoch_time(x)``: a UTC ISO-8601 string, which
    is what any reader of these documents got after JSON serialisation."""
    return dt.datetime.fromtimestamp(epoch_seconds, dt.timezone.utc).isoformat()


class intellistockAPI:

    # GLOBAL VAR
    def __init__(self, instance, ticker, db, mode):
        # Use the application default credentials
        self.db = db
        self.instance = instance
        
        self.ticker = ticker
        self.endpoint = 'https://intellistockapi.loca.lt'
        self.backupEndpoint = 'http://localhost:3101'
        self.connection = None      # the store takes its own connection


        print("Intellistock class connected to firebase")

        if(mode != 'instance'):

            table = store.run(store.filter('LivePricesStocks', {'ticker': self.ticker}))

            inTable = False

            for doc in table:
                ticker = doc['ticker']
                if(ticker == self.ticker):
                    inTable = True
            

            if(inTable == False):
                store.insert('LivePricesStocks', {
                    "id": self.ticker,
                    "ticker": self.ticker,
                    "price": 0,
                    "timestamp": 0,
                })

                store.insert('LivePrices', {
                    "id": self.ticker,
                    "ticker": self.ticker,
                    "price": 0,
                    #"timestamp": timestamp,
                })


    
    def getPrice(self):
        table = store.run(store.filter('LivePrices', {'id': self.ticker}))

        price = 0

        for doc in table:
            #print(doc)
            price = doc['price']


        #print(price)
        
        return price



        


    def updatePrice(self, time, calendarTime, price):
        now = dt.datetime.now()

        current_time = str(now.strftime("%H:%M:%S"))
        current_day = now.weekday()

        start = '06:30:00' #  Market open time
        end = '13:01:00' #  Market close time

        if(current_day != 5 and current_day != 6 and (current_time > start and current_time < end)):
            
            """
            conn = sqlite3.connect('D:/data.db')

            c = conn.cursor()

            try:
                c.execute("INSERT INTO prices VALUES ('" + self.ticker +"', " + str(price) + ", " + str(calendar.timegm(calendarTime)) + ", '" + self.instance + "', " + str(now.day) + ", " + str(now.month) + ", " + str(now.year) + ")")
            except Exception as e:
                c.execute('''CREATE TABLE prices(ticker text, price real, timestamp int, instance text, day int, month int, year int)''')
                c.execute("INSERT INTO prices VALUES ('" + self.ticker +"'," + str(price) + ", " + str(calendar.timegm(calendarTime)) + ", '" + self.instance + "', " + str(now.day) + ", " + str(now.month) + ", " + str(now.year) + ")")
                print(e)
            
            # Save (commit) the changes
            conn.commit()

            # We can also close the connection if we are done with it.
            # Just be sure any changes have been committed or they will be lost.
            conn.close()
            """

            timestamp = calendar.timegm(calendarTime)

            try:
                store.insert('Prices', {
                    "id": str(uuid.uuid4()),
                    "ticker": self.ticker,
                    "price": price,
                    "timestamp": timestamp,
                    "instance": self.instance,
                    "day": now.day,
                    "month": now.month,
                    "year": now.year,
                    "datetime": _epoch_to_iso(timestamp),
                })

            except Exception as e:
                print(colored("[ERROR][" + str(dt.datetime.now().strftime("%H:%M:%S")) + "] Update ticker price error RETHINK. Skipping", 'red'))
                logging.exception('message')
                pass


            #try:
                #response = requests.get(self.endpoint + f'/updatePrice?ticker={self.ticker}&strTime={str(time)}&price={price}&timestamp={str(calendar.timegm(calendarTime))}&instance={self.instance}')


            #except:
                #print(colored("[ERROR][" + str(dt.datetime.now().strftime("%H:%M:%S")) + "] Update ticker JSON error. Skipping", 'red'))
                #response = requests.get(self.backupEndpoint + f'/updatePrice?ticker={self.ticker}&strTime={str(time)}&price={price}&timestamp={str(calendar.timegm(calendarTime))}&instance={self.instance}')
                #logging.exception('message')
                #pass
    
    
    def test(self, x):
        try:
            print(int(x))
        except:
            print(colored("[ERROR][" + str(dt.datetime.now().strftime("%H:%M:%S")) + "] Update ticker tranasction JSON error. Skipping", 'red'))
            logging.exception('message')
            pass
    
    def sendNotification(self, title, message, notifyType):
        calendarTime = time.gmtime()

        data = {
            "title": title,
            "message": message,
            "type": notifyType,
            "timestamp": calendar.timegm(calendarTime) * 1000
        }

        self.db.collection(u'Instances').document(self.instance).collection(u'Notifications').document().set(data)

    def updateTransaction(self, time, price, calendarTime, action, shares):
        now = dt.datetime.now()

        current_time = str(now.strftime("%H:%M:%S"))
        current_day = now.weekday()

        start = '06:30:00' #  Market open time
        end = '13:01:00' #  Market close time

        actionText = ""

        if(action == "BUY"):
            actionText == "bought "
        else:
            actionText == "sold "
    

        self.sendNotification("Order confirmation", "The service has " + actionText + " " + str(shares) + " shares of " + self.ticker + " for $" + str(price) + " per share.", "transaction")

        if(current_day != 5 and current_day != 6 and (current_time > start and current_time < end)):

            """
            conn = sqlite3.connect('D:/data.db')

            c = conn.cursor()

            try:
                c.execute("INSERT INTO transactions VALUES ('" + action + "','" + self.ticker +"'," + str(shares) + ", " + str(price) + ", " + str(calendar.timegm(calendarTime)) + ", '" + self.instance + "', " + str(now.day) + ", " + str(now.month) + ", " + str(now.year) + ")")
            except Exception as e:
                c.execute('''CREATE TABLE transactions(type text, ticker text, quantity int, price real, timestamp int, instance text, day int, month int, year int)''')
                c.execute("INSERT INTO transactions VALUES ('" + action + "','" + self.ticker +"'," + str(shares) + ", " + str(price) + ", " + str(calendar.timegm(calendarTime)) + ", '" + self.instance + "', " + str(now.day) + ", " + str(now.month) + ", " + str(now.year) + ")")
                print(e)
            
            # Save (commit) the changes
            conn.commit()

            # We can also close the connection if we are done with it.
            # Just be sure any changes have been committed or they will be lost.
            conn.close()
            """

            timestamp = calendar.timegm(calendarTime)

            try:
                store.insert('Transactions', {
                    "id": str(uuid.uuid4()),
                    "type": action,
                    "ticker": self.ticker,
                    "quantity": shares,
                    "timestamp": timestamp,
                    "instance": self.instance,
                    "price": price,
                    "day": now.day,
                    "month": now.month,
                    "year": now.year,
                    "datetime": _epoch_to_iso(timestamp),
                })

            except Exception as e:
                print(colored("[ERROR][" + str(dt.datetime.now().strftime("%H:%M:%S")) + "] Update transaction error RETHINK. Skipping", 'red'))
                logging.exception('message')
                pass


            #try:
                #response = requests.get(self.endpoint + f'/updateTransaction?ticker={self.ticker}&strTime={str(time)}&price={price}&timestamp={str(calendar.timegm(calendarTime))}&action={str(action)}&instance={self.instance}')


            #except:
                #print(colored("[ERROR][" + str(dt.datetime.now().strftime("%H:%M:%S")) + "] Update ticker tranasction JSON error. Skipping", 'red'))
                #response = requests.get(self.backupEndpoint + f'/updatePrice?ticker={self.ticker}&strTime={str(time)}&price={price}&timestamp={str(calendar.timegm(calendarTime))}&instance={self.instance}')
                #logging.exception('message')
                #pass
    
        