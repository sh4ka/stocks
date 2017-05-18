#!/usr/bin/env python

import sys
import time
import datetime
from datetime import  date, timedelta
from models import Base, Symbol, Quote, Indicator
from numpy import array, asarray
from sqlalchemy import create_engine, desc
from sqlalchemy.orm import sessionmaker, joinedload, eagerload
from sqlalchemy.sql import and_


import config as cfg
import indicators

sys.path.insert(0, '../sources')
import yahoofinance as quotes #sources

class Database(object):

    def __init__(self):
        """
        Set up database access
        """
        self.Base = Base

        # Handle edge case here
        if cfg.STOCKS_SQL_PASSWORD == '':
            engine_config = 'mysql://%s@%s/%s' % (cfg.STOCKS_SQL_USER,
                                                  cfg.STOCKS_SQL_HOSTNAME,
                                                  cfg.STOCKS_SQL_DATABASE)
        else:
            engine_config = 'mysql://%s:%s@%s/%s' % (cfg.STOCKS_SQL_USER,
                                                     cfg.STOCKS_SQL_PASSWORD,
                                                     cfg.STOCKS_SQL_HOSTNAME,
                                                     cfg.STOCKS_SQL_DATABASE)
        self.Engine = create_engine(engine_config)
        self.Session = sessionmaker()
        self.Session.configure(bind=self.Engine)


class Manager(object):
    """ Stock Database Manager

    This is used to manage the stock database
    """

    def __init__(self):
        self.db = Database()

    def create_database(self):
        """ Create stock database tables if they do not exist already

        """
        self.db.Base.metadata.create_all(self.db.Engine)

    def add_stock(self, ticker, name=None, exchange=None,
                  sector=None, industry=None):
        """ Add a stock to the stock database
        Add the stock to the symbols table and populate quotes table with all
        available historical quotes. If any of the optional parameters are left
        out, the corresponding information will be obtained from Yahoo!
        Finance.
        :param ticker: Stock ticker symbol
        :param name: (optional) Company/security name
        :param exchange: (optional) Exchange on which the security is traded
        :param sector: (optional) Company/security sector
        :param Industry (optional) Company/security industry
        """
        ticker = ticker.lower()
        session = self.db.Session()

        if self.check_stock_exists(ticker, session):
            print "Stock %s already exists!" % (ticker.upper())
            return

        if name is None:
            name = quotes.get_name(ticker)
        if exchange is None:
            exchange = quotes.get_stock_exchange(ticker)
        if sector is None:
            sector = quotes.get_sector(ticker)
        if industry is None:
            industry = quotes.get_industry(ticker)

        stock = Symbol(ticker, name, exchange, sector, industry)

        session.add(stock)
        q = self._download_quotes(ticker, date(1900, 01, 01), date.today())
        for quote in q:
            quote.Features = Indicator(quote.Id)
        session.add_all(q)
        session.commit()
        session.close()
        self.update_quotes(ticker)

    def delete_symbol(self, ticker):
        """ Delete a symbol with all its children from the database
        :param ticker: Stock ticker symbol
        """
        ticker = ticker.lower()
        session = self.db.Session()

        if not self.check_stock_exists(ticker, session):
            print "Stock %s already exists!" % (ticker.upper())
            return

        symbol = session.query(Symbol).filter_by(Ticker=ticker.lower()).first()
        print "Deleting %s. This can take a while!" % (ticker.upper())
        session.delete(symbol)
        session.commit()
        session.close()


    def _download_quotes(self, ticker, start_date, end_date):
        """ Get quotes from Yahoo Finance
        """
        ticker = ticker.lower()
        if start_date == end_date:
            return
        start = start_date
        end = end_date
        data = quotes.get_historical_prices(ticker, start, end)
        data = data[len(data) - 1:0:-1]
        if len(data):
            return [Quote(ticker, val[0], val[1], val[2],
                          val[3], val[4], val[5], val[6])
                    for val in data if len(val) > 6]
        else:
            return

    def _calculate_indicators(self, ticker):
        """ Calculate indicators and add to indicators table
        """
        ticker = ticker.lower()
        session = self.db.Session()
        data = asarray(zip(*[(int(quote.Id), quote.AdjClose)
                             for quote in session.query(Quote)
                             .filter_by(Ticker=ticker)
                             .order_by(Quote.Date).all()]))
        for ind in indicators.calculate_all(data):
            if not self.check_indicator_exists(ind.Id, session):
                session.add(ind)
        session.commit()
        session.close()

    def update_quotes(self, ticker, check_all=True):
        """
        Get all missing quotes through current day for the given stock
        """
        ticker = ticker.lower()
        stockquotes = None
        session = self.db.Session()
        last = session.query(Quote).filter_by(
            Ticker=ticker).order_by(desc(Quote.Date)).first().Date
        start_date = last + timedelta(days=1)
        # Ignore missing quotes for today unless it's after 7, this keeps
        # us from hitting the yahoo API when we know the data isn't there yet
        end_date = (date.today() if datetime.datetime.now().time() >
                datetime.time(19) else date.today() - timedelta(days=1))
        if end_date > start_date:
            stockquotes = self._download_quotes(ticker, start_date, end_date)
            # Appease the API rate limit gods????
            time.sleep(10)
            if stockquotes is not None:
                for quote in stockquotes:
                    quote.Features = Indicator(quote.Id)
                session.add_all(stockquotes)
        #indicators.update_all(ticker, session, False, check_all)
        indicators.update_all(ticker, session, True, check_all)
        session.commit()
        session.close()

    def sync_quotes(self, check_all=False):
        """
        Updates quotes for all stocks through current day.
        """
        for symbol in self._stocks():
            self.update_quotes(symbol, check_all)
            print 'Updated quotes for %s' % symbol

    def check_stock_exists(self, ticker, session=None):
        """
        Return true if stock is already in database
        """
        newsession = False
        if session is None:
            newsession = True
            session = self.db.Session()
        exists = bool(
            session.query(Symbol).filter_by(Ticker=ticker.lower()).count())
        if newsession:
            session.close()
        return exists

    def check_quote_exists(self, ticker, q_date, session=None):
        """
        Return true if a quote for the given symbol and date exists in the
        database
        """
        newsession = False
        if session is None:
            newsession = True
            session = self.db.Session()
        exists = bool(session.query(Symbol).filter_by(Ticker=ticker.lower(),
                                                      Date=q_date).count())
        if newsession:
            session.close()
        return exists

    def check_indicator_exists(self, qid, session=None):
        """ Return True if indicator is already in database
        """
        newsession = False
        if session is None:
            newsession = True
            session = self.db.Session()
        exists = bool(session.query(Indicator).filter_by(Id=qid).count())
        if newsession:
            session.close()
        return exists


    def _stocks(self, session=None):
        newsession = False
        if session is None:
            newsession = True
            session = self.db.Session()
        stocks = array([stock.Ticker for stock in session.query(Symbol).all()])
        if newsession:
            session.close()
        return stocks


class Client(object):
    """ Stock database client

    The stock database client is used to access the stock database.
    """

    def __init__(self):
        self.db = Database()
        self.manager = Manager()

    def get_quotes(self, ticker, quote_date, end_date=None, eager_load=False):
        """
        Return a list of quotes between the start date and (optional) end date.
        if no end date is specified, return a list containing the quote for the
        start date.

        :param ticker: Stock ticker symbol
        :param quote_date:  Starting date for quotes to retrieve.
        :param end_date: (optional) if more than one quote is desired, the
        ending date for the list of quotes.
        """
        ticker = ticker.lower()
        session = self.db.Session()
        stockquotes = []
        if not self.manager.check_stock_exists(ticker, session):
            self.manager.add_stock(ticker)
        if eager_load:
            loader = eagerload
        else:
            loader = lazyload

        if end_date is not None:
            query = session.query(Quote).options(loader('Features')).filter(and_(Quote.Ticker == ticker,
                                                     Quote.Date >= quote_date,
                                                     Quote.Date <= end_date))\
                .order_by(Quote.Date)
        else:
            query = session.query(Quote).options(loader('Features')).filter(and_(Quote.Ticker == ticker,
                                                     Quote.Date == quote_date))

        stockquotes = [quote for quote in query.all()]
        session.close()
        return stockquotes

    def stocks(self, session=None):
        """
        Return a list of the stocks available in the database
        """
        newsession = False
        if session is None:
            newsession = True
            session = self.db.Session()
        stocks = array([stock.Ticker for stock in session.query(Symbol).all()])
        if newsession:
            session.close()
        return stocks



if __name__ == '__main__':
    from sys import argv
    if len(argv) > 1:
        db = Manager()
        opt = str(argv[1])


        if opt == 'create':
            db.create_database()

        elif opt == 'sync':
            check_all = len(argv) > 2
            db.sync_quotes(check_all)

        elif opt == 'add':
            db.add_stock(str(argv[2]))

        elif opt == 'update':
            db.update_quotes(str(argv[2]))

        elif opt == 'delete':
            db.delete_symbol(str(argv[2]))

    else:
        exit('No command specified. Exiting.')
