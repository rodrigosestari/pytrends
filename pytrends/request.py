from __future__ import absolute_import, print_function, unicode_literals

import json
import time
from datetime import datetime, timedelta
from urllib.parse import quote

import pandas as pd
import requests
from pandas.io.json.normalize import nested_to_record

from pytrends import exceptions


class TrendReq(object):
    """
    Google Trends API
    """

    GET_METHOD = 'get'
    POST_METHOD = 'post'

    GENERAL_URL = 'https://trends.google.com/trends/api/explore'
    INTEREST_OVER_TIME_URL = 'https://trends.google.com/trends/api/widgetdata/multiline'
    INTEREST_BY_REGION_URL = 'https://trends.google.com/trends/api/widgetdata/comparedgeo'
    RELATED_QUERIES_URL = 'https://trends.google.com/trends/api/widgetdata/relatedsearches'
    TOP_DAILY_URL = 'https://trends.google.com/trends/api/dailytrends'
    TRENDING_REALTIME_URL = 'https://trends.google.com/trends/api/realtimetrends'
    SUGGESTIONS_URL = 'https://trends.google.com/trends/api/autocomplete/'
    CATEGORIES_URL = 'https://trends.google.com/trends/api/explore/pickers/category'

    def __init__(self, hl='en-US', tz=360, geo='', proxies=''):
        """
        Initialize default values for params
        """
        # google rate limit
        self.google_rl = 'You have reached your quota limit. Please try again later.'
        self.results = None

        # set user defined options used globally
        self.tz = tz
        self.hl = hl
        self.geo = geo
        self.kw_list = list()
        self.session = requests.session()
        self.session.headers.update({'accept-language': self.hl})
        if proxies != '':
            self.session.proxies.update(proxies)
        # proxies format: {"http": "http://192.168.0.1:8888" , "https": "https://192.168.0.1:8888"}
        self.cookies = dict(filter(
            lambda i: i[0] == 'NID',
            self.session.get(
                'https://trends.google.com/?geo={geo}'.format(geo=hl[-2:]),
                timeout=5,
            ).cookies.items()
        ))

        # intialize widget payloads
        self.token_payload = dict()
        self.interest_over_time_widget = dict()
        self.interest_by_region_widget = dict()
        self.related_topics_widget_list = list()
        self.related_queries_widget_list = list()

    def _get_data(self, url, method=GET_METHOD, trim_chars=0, **kwargs):
        """Send a request to Google and return the JSON response as a Python object

        :param url: the url to which the request will be sent
        :param method: the HTTP method ('get' or 'post')
        :param trim_chars: how many characters should be trimmed off the beginning of the content of the response
            before this is passed to the JSON parser
        :param kwargs: any extra key arguments passed to the request builder (usually query parameters or data)
        :return:
        """

        if method == TrendReq.POST_METHOD:
            response = self.session.post(url, cookies=self.cookies, **kwargs)
        else:
            response = self.session.get(url, cookies=self.cookies, **kwargs)

        # check if the response contains json and throw an exception otherwise
        # Google mostly sends 'application/json' in the Content-Type header,
        # but occasionally it sends 'application/javascript
        # and sometimes even 'text/javascript
        if 'application/json' in response.headers['Content-Type'] or \
                'application/javascript' in response.headers['Content-Type'] or \
                'text/javascript' in response.headers['Content-Type']:

            # trim initial characters
            # some responses start with garbage characters, like ")]}',"
            # these have to be cleaned before being passed to the json parser
            content = response.text[trim_chars:]

            # parse json
            return json.loads(content)
        else:
            # this is often the case when the amount of keywords in the payload for the IP
            # is not allowed by Google
            raise exceptions.ResponseError('The request failed: Google returned a '
                                           'response with code {0}.'.format(response.status_code), response=response)

    def build_payload(self, kw_list, cat=0, timeframe='today 5-y', geo='', gprop=''):
        """Create the payload for related queries, interest over time and interest by region"""
        self.kw_list = kw_list
        self.geo = geo
        self.token_payload = {
            'hl': self.hl,
            'tz': self.tz,
            'req': {'comparisonItem': [], 'category': cat, 'property': gprop}
        }
        if len(kw_list) > 0:
            # build out json for each keyword
            for kw in self.kw_list:
                self.token_payload['req']['comparisonItem'].append({'keyword': kw, 'time': timeframe, 'geo': self.geo})
        else:
            self.token_payload['req']['comparisonItem'].append({'time': timeframe, 'geo': self.geo})
        # requests will mangle this if it is not a string
        self.token_payload['req'] = json.dumps(self.token_payload['req'])
        # get tokens
        self._tokens()
        return

    def _tokens(self):
        """Makes request to Google to get API tokens for interest over time, interest by region and related queries"""

        # make the request and parse the returned json
        widget_dict = self._get_data(
            url=TrendReq.GENERAL_URL,
            method=TrendReq.GET_METHOD,
            params=self.token_payload,
            trim_chars=4,
        )['widgets']

        # order of the json matters...
        first_region_token = True
        # clear self.related_queries_widget_list and self.related_topics_widget_list
        # of old keywords'widgets
        self.related_queries_widget_list[:] = []
        self.related_topics_widget_list[:] = []
        # assign requests
        for widget in widget_dict:
            if widget['id'] == 'TIMESERIES':
                self.interest_over_time_widget = widget
            if widget['id'] == 'GEO_MAP' and first_region_token:
                self.interest_by_region_widget = widget
                first_region_token = False
            # response for each term, put into a list
            if 'RELATED_TOPICS' in widget['id']:
                self.related_topics_widget_list.append(widget)
            if 'RELATED_QUERIES' in widget['id']:
                self.related_queries_widget_list.append(widget)
        return

    def interest_over_time(self):
        """Request data from Google's Interest Over Time section and return a dataframe"""

        over_time_payload = {
            # convert to string as requests will mangle
            'req': json.dumps(self.interest_over_time_widget['request']),
            'token': self.interest_over_time_widget['token'],
            'tz': self.tz
        }

        # make the request and parse the returned json
        req_json = self._get_data(
            url=TrendReq.INTEREST_OVER_TIME_URL,
            method=TrendReq.GET_METHOD,
            trim_chars=5,
            params=over_time_payload,
        )

        df = pd.DataFrame(req_json['default']['timelineData'])
        if (df.empty):
            return df

        df['date'] = pd.to_datetime(df['time'].astype(dtype='float64'), unit='s')
        df = df.set_index(['date']).sort_index()
        # split list columns into seperate ones, remove brackets and split on comma
        result_df = df['value'].apply(lambda x: pd.Series(str(x).replace('[', '').replace(']', '').split(',')))
        # rename each column with its search term, relying on order that google provides...
        for idx, kw in enumerate(self.kw_list):
            # there is currently a bug with assigning columns that may be
            # parsed as a date in pandas: use explicit insert column method
            result_df.insert(len(result_df.columns), kw, result_df[idx].astype('int'))
            del result_df[idx]

        if 'isPartial' in df:
            # make other dataframe from isPartial key data
            # split list columns into seperate ones, remove brackets and split on comma
            df = df.fillna(False)
            result_df2 = df['isPartial'].apply(lambda x: pd.Series(str(x).replace('[', '').replace(']', '').split(',')))
            result_df2.columns = ['isPartial']
            # concatenate the two dataframes
            final = pd.concat([result_df, result_df2], axis=1)
        else:
            final = result_df
            final['isPartial'] = False

        return final

    def interest_by_region(self, resolution='COUNTRY', inc_low_vol=False, inc_geo_code=False):
        """Request data from Google's Interest by Region section and return a dataframe"""

        # make the request
        region_payload = dict()
        if self.geo == '':
            self.interest_by_region_widget['request']['resolution'] = resolution
        elif self.geo == 'US' and resolution in ['DMA', 'CITY', 'REGION']:
            self.interest_by_region_widget['request']['resolution'] = resolution

        self.interest_by_region_widget['request']['includeLowSearchVolumeGeos'] = inc_low_vol

        # convert to string as requests will mangle
        region_payload['req'] = json.dumps(self.interest_by_region_widget['request'])
        region_payload['token'] = self.interest_by_region_widget['token']
        region_payload['tz'] = self.tz

        # parse returned json
        req_json = self._get_data(
            url=TrendReq.INTEREST_BY_REGION_URL,
            method=TrendReq.GET_METHOD,
            trim_chars=5,
            params=region_payload
        )
        df = pd.DataFrame(req_json['default']['geoMapData'])
        if (df.empty):
            return df

        # rename the column with the search keyword
        df = df[['geoName', 'geoCode', 'value']].set_index(['geoName']).sort_index()
        # split list columns into seperate ones, remove brackets and split on comma
        result_df = df['value'].apply(lambda x: pd.Series(str(x).replace('[', '').replace(']', '').split(',')))
        if inc_geo_code:
            result_df['geoCode'] = df['geoCode']

        # rename each column with its search term
        for idx, kw in enumerate(self.kw_list):
            result_df[kw] = result_df[idx].astype('int')
            del result_df[idx]

        return result_df

    def related_topics(self):
        """Request data from Google's Related Topics section and return a dictionary of dataframes

        If no top and/or rising related topics are found, the value for the key "top" and/or "rising" will be None
        """

        # make the request
        related_payload = dict()
        result_dict = dict()
        for request_json in self.related_topics_widget_list:
            # ensure we know which keyword we are looking at rather than relying on order
            if request_json['request']['restriction'].get("complexKeywordsRestriction") is not None:
                kw = request_json['request']['restriction']['complexKeywordsRestriction']['keyword'][0]['value']
            else:
                kw = ""

            # convert to string as requests will mangle
            related_payload['req'] = json.dumps(request_json['request'])
            related_payload['token'] = request_json['token']
            related_payload['tz'] = self.tz

            # parse the returned json
            req_json = self._get_data(
                url=TrendReq.RELATED_QUERIES_URL,
                method=TrendReq.GET_METHOD,
                trim_chars=5,
                params=related_payload,
            )

            # top topics
            try:
                top_list = req_json['default']['rankedList'][0]['rankedKeyword']
                df_top = pd.DataFrame([nested_to_record(d, sep='_') for d in top_list])
            except KeyError:
                # in case no top topics are found, the lines above will throw a KeyError
                df_top = None

            # rising topics
            try:
                rising_list = req_json['default']['rankedList'][1]['rankedKeyword']
                df_rising = pd.DataFrame([nested_to_record(d, sep='_') for d in rising_list])
            except KeyError:
                # in case no rising topics are found, the lines above will throw a KeyError
                df_rising = None

            result_dict[kw] = {'rising': df_rising, 'top': df_top}
        return result_dict

    def related_queries(self):
        """Request data from Google's Related Queries section and return a dictionary of dataframes

        If no top and/or rising related queries are found, the value for the key "top" and/or "rising" will be None
        """

        # make the request
        related_payload = dict()
        result_dict = dict()
        for request_json in self.related_queries_widget_list:
            # ensure we know which keyword we are looking at rather than relying on order
            if request_json['request']['restriction'].get("complexKeywordsRestriction") is not None:
                kw = request_json['request']['restriction']['complexKeywordsRestriction']['keyword'][0]['value']
            else:
                kw = ""

            # convert to string as requests will mangle
            related_payload['req'] = json.dumps(request_json['request'])
            related_payload['token'] = request_json['token']
            related_payload['tz'] = self.tz

            # parse the returned json
            req_json = self._get_data(
                url=TrendReq.RELATED_QUERIES_URL,
                method=TrendReq.GET_METHOD,
                trim_chars=5,
                params=related_payload,
            )

            # top queries
            try:
                top_df = pd.DataFrame(req_json['default']['rankedList'][0]['rankedKeyword'])
                top_df = top_df[['query', 'value']]
            except KeyError:
                # in case no top queries are found, the lines above will throw a KeyError
                top_df = None

            # rising queries
            try:
                rising_df = pd.DataFrame(req_json['default']['rankedList'][1]['rankedKeyword'])
                rising_df = rising_df[['query', 'value']]
            except KeyError:
                # in case no rising queries are found, the lines above will throw a KeyError
                rising_df = None

            result_dict[kw] = {'top': top_df, 'rising': rising_df}
        return result_dict

    def trending_realtime(self, hl='en-US', tz=360, cat='all', geo='US'):
        # Request data from Google's Trending Searches section and return a dataframe

        # make the request
        forms = {'hl': hl, 'tz': tz, 'cat': cat, 'geo': geo, 'fi': 0, 'fs': 0, 'ri': 300, 'rs': 20}
        req_json = self._get_data(
            url=TrendReq.TRENDING_REALTIME_URL,
            method=TrendReq.GET_METHOD,
            trim_chars=5,
            params=forms
        )["storySummaries"]['trendingStories']

        # parse the returned json
        sub_df = []
        for trend in req_json:
            sub_df.append(trend['title'])
        result_df = pd.DataFrame(sub_df)
        return result_df

    def top_daily(self, geo='US'):
        # Request data from Google's Top Daily section and return a dataframe

        # create the payload
        chart_payload = {'geo': geo}

        # make the request and parse the returned json
        req_json = self._get_data(
            url=TrendReq.TOP_DAILY_URL,
            method=TrendReq.GET_METHOD,
            trim_chars=5,
            params=chart_payload,
        )['default']['trendingSearchesDays']

        # parse the returned json
        sub_df = []
        for trend_day in req_json:
            for trend in trend_day['trendingSearches']:
                sub_df.append((trend_day['date'], trend['title']['query']))
        df = pd.DataFrame(sub_df)
        return df

    def suggestions(self, keyword):
        """Request data from Google's Keyword Suggestion dropdown and return a dictionary"""

        # make the request
        kw_param = quote(keyword)
        parameters = {'hl': self.hl}

        req_json = self._get_data(
            url=TrendReq.SUGGESTIONS_URL + kw_param,
            params=parameters,
            method=TrendReq.GET_METHOD,
            trim_chars=5
        )['default']['topics']
        return req_json

    def categories(self):
        """Request available categories data from Google's API and return a dictionary"""

        params = {'hl': self.hl}

        req_json = self._get_data(
            url=TrendReq.CATEGORIES_URL,
            params=params,
            method=TrendReq.GET_METHOD,
            trim_chars=5
        )
        return req_json

    def get_historical_interest(self, keywords, year_start=2018, month_start=1, day_start=1, hour_start=0,
                                year_end=2018, month_end=2, day_end=1, hour_end=0, cat=0, geo='', gprop='', sleep=0):
        """Gets historical hourly data for interest by chunking requests to 1 week at a time (which is what Google allows)"""

        # construct datetime obejcts - raises ValueError if invalid parameters
        start_date = datetime(year_start, month_start, day_start, hour_start)
        end_date = datetime(year_end, month_end, day_end, hour_end)

        # the timeframe has to be in 1 week intervals or Google will reject it
        delta = timedelta(days=31)

        df = pd.DataFrame()

        date_iterator = start_date
        date_iterator += delta - timedelta(hours=1)

        done = False
        while not done:
            # format date to comply with API call
            start_date_str = start_date.strftime('%Y-%m-%d')  # T%H')
            date_iterator_str = date_iterator.strftime('%Y-%m-%d')  # T%H')
            tf = start_date_str + ' ' + date_iterator_str

            try:
                self.build_payload(keywords, cat, tf, geo, gprop)
                week_df = self.interest_over_time()
                if (date_iterator >= end_date):
                    done = True
                    # Cut down results to only go up to end date.
                    end_date_str = end_date.strftime('%Y-%m-%d')
                    week_df = week_df[:end_date_str]
                df = df.append(week_df)

            except Exception as e:
                print(e)
                pass

            start_date += delta
            date_iterator += delta

            # just in case you are rate-limited by Google. Recommended is 60 if you are.
            if sleep > 0:
                time.sleep(sleep)

        return df
