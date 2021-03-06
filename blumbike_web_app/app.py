# blum.bike Web App
# Copyright 2020 Jeremy Blum, Blum Idea Labs, LLC.
# www.jeremyblum.com

# This code is licensed under MIT license (see LICENSE.md for details)

# Import what we need
import os
import re
import redis
import dash
import time
from functools import wraps
import datetime
from natural import date
import json
import dash_bootstrap_components as dbc
import dash_html_components as html
import dash_core_components as dcc
from dash.exceptions import PreventUpdate
from plotly.subplots import make_subplots
from dash.dependencies import Input, Output, ALL
from flask import request
import requests

# These constants should match what is configured in the Photon firmware
MIN_RESISTANCE = 1
MAX_RESISTANCE = 10

# Initialize the app
app = dash.Dash(__name__, external_stylesheets=[dbc.themes.SOLAR], update_title=None)
app.title = "blum.bike"
server = app.server  # This is the Flask Parent Server that we can use to receive webhooks
server.config['SECRET_KEY'] = os.environ.get("SECRET_KEY")

# Connect to Redis for persistent storage of session data
r = redis.from_url(os.environ.get("REDIS_URL"), decode_responses=True)

# Define the dashboard layout
sidebar =   dbc.Col(children=[
                        html.Div(id='control-sidebar', hidden=True, children=[
                            html.H2("blum.bike Resistance Control", className="card-header"),
                            html.Div(id='control-panel', children=[
                                dbc.Button('Decrease Resistance', id={"index": "down", "type": "resistance"}, color="info", style={"width": "40%", "margin": "0px 5%"}, n_clicks=0, disabled=False, className="shadow-none"),
                                dbc.Button('Increase Resistance', id={"index": "up", "type": "resistance"}, color="info", style={"width": "40%", "margin": "0px 5%"}, n_clicks=0, disabled=False, className="shadow-none"),
                            ], className="card-body bs-component", style={"display": "flex", "width": "100%"}),
                            dbc.Alert("Command Status Alert", id="resistance-status", is_open=False, duration=3000, style={"width": "80%", "textAlign": "center", "margin": "0px 10% 10px"}),
                            html.Div(id='control-panel-footer', children=[], className="card-footer text-muted")
                        ], className='card mb-3'),
                        html.Div(id='stats-sidebar', children=[
                            html.H2("blum.bike Stats", className="card-header"),
                            html.Div(id='live-update-body', className="card-body"),
                            html.Div(id='live-update-footer', className="card-footer text-muted")
                        ], className='card mb-3')
                    ],
                    className='col-12 col-sm-12 col-md-12 col-lg-5 col-xl-4 sidebar'
                    )

content =   dbc.Col(className='col-12 col-sm-12 col-md-12 col-lg-7 col-lg-offset-5 col-xl-8 col-xl-offset-4 main',
                    children=[
                        html.Div(id='graph-spinner', style={'textAlign': 'center'},
                                 children=[
                                     dbc.Spinner(color="primary"),
                                     html.P('Loading Graphs...')
                                     ]
                                 ),
                        html.Div(id='live-graph-div', style={'visibility': 'hidden'}, # Starts Hidden so the Graph can load first
                                 children=[
                                    dcc.Graph(id='live-update-graph', config={'displayModeBar': False})
                                 ]),
                        dcc.Interval(id='interval-component-fast', interval=1000, n_intervals=0),
                        dcc.Interval(id='interval-component-slow', interval=5000, n_intervals=0)
                    ])

main = dbc.Row(children=[sidebar, content], id='main-content')

footer = html.Footer(
            dbc.Row(
                dbc.Col(
                    children=[
                        html.Hr(),
                        html.P(
                            html.Small(
                                children=[
                                    "© 2020 ",
                                    html.A('Jeremy Blum', href='https://www.jeremyblum.com'),
                                    ", ",
                                    html.A('Blum Idea Labs, LLC.', href='https://www.blumidealabs.com'),
                                    html.Br(),
                                    "blum.bike is still a work-in-progress. But it is open source! Learn more in the ",
                                    html.A('blum.bike GitHub Repo', href='https://github.com/sciguy14/blumbike'),
                                    "."
                                ]
                            )
                        )
                    ],
                    className='col-lg-12',
                    style={'textAlign': 'center'}
                )
            ),
            className="footer"
        )

app.layout = dbc.Container([main, footer], style={'padding': '15px'}, fluid=True)


# A decorator function to require an api key for pushing data to this application from the Particle webhook
# https://coderwall.com/p/4qickw/require-an-api-key-for-a-route-in-flask-using-only-a-decorator
def require_apikey(view_function):
    @wraps(view_function)
    # the new, post-decoration function. Note *args and **kwargs here.
    def decorated_function(*args, **kwargs):
        if request.json['apikey'] and "apikey" in os.environ and request.json['apikey'] == str(os.environ.get("apikey")):
            return view_function(*args, **kwargs)
        else:
            print("invalid api key match")
            return {"reply": "invalid key"}, 401
    return decorated_function


# Receive incoming data as POST JSON objects from the Particle Cloud
@server.route('/update', methods=['POST'])
@require_apikey
def rest_update():
    latest_data = json.loads(request.json['data'])

    # The "event" key will be:
    # "powered_on" when the Photon is turned on
    # "start_session" when the Photon has detected that a new session has started
    # "end_session" when the Photon has detected that a session has ended
    # "new_data" for new bike stats
    if latest_data['event'] == "powered_on":
        # This triggers when the photon is powered on
        # Note when this session started
        r.set("powered_on", latest_data['t'])
        print("BIKE POWERED ON: {}".format(latest_data))
        return {"reply": "power on received"}

    if latest_data['event'] == "start_session":
        # When user has initiated a new session (sequential non-zero dyno RPMs), we clear all existing redis data
        r.flushdb()
        # Note when this session started
        r.set("session_start", latest_data['t'])
        # The particle's public IP will also be sent at session start. We save this and show resistance control to clients originating from the same IP
        r.set("bike_ip", latest_data['ip'])
        print("STARTED A NEW SESSION: {}".format(latest_data))
        return {"reply": "started session"}

    if latest_data['event'] == "end_session":
        # When user has finished a new session (sequential non-zero dyno RPMs), we can note that in the UI
        r.set("session_end", latest_data['t'])
        r.delete("bike_ip")
        time.sleep(.1)  # Briefly sleep after the end of a session to ensure the session end is set in redis
        print("ENDED THE SESSION: {}".format(latest_data))
        return {"reply": "ended session"}

    elif latest_data['event'] == "new_data":
        # If a value comes in out of order, discard it.
        if (r.exists('timestamp') and int(r.lindex('timestamp', 0)) > int(latest_data['t'])) or r.exists('session_end'):
            print("IGNORED (STALE): {}".format(latest_data))
            return {"reply": "ignored stale data"}

        # Push the data into a running list in redis
        r.lpush('timestamp', latest_data['t'])
        r.lpush('bike_mph', latest_data['bike_mph'])
        r.lpush('resistance', latest_data['resistance'])
        r.lpush('heart_bpm', latest_data['heart_bpm'])

        # Keep the list trimmed (disabled. Shouldn't be necessary to trim the list since we end sessions when the bike stops).
        # r.ltrim('timestamp', 0, 300)
        # r.ltrim('bike_mph', 0, 300)
        # r.ltrim('heart_bpm', 0, 300)

        print("APPENDED: {}".format(latest_data))
        return {"reply": "data appended"}

    else:
        print("APPENDED: {}".format(latest_data))
        return {"reply": "event '{}' not understood".format(latest_data['event'])}, 501


# This callback generates the control sidebar by checking if user is authorized and unhiding the control buttons.
# The user is authorized to control bike resistance if their originating Public IP matches that of the Particle Photon that is sending updates.
# When the bike starts a session, the Photon gets its own public IP address and transmits it with the session info. We compare against that value.
# This is obviously not immune from being compromised, since IPs can be spoofed and proxied.
# The div can also just easily be unhidden using the browser inspection tools, but we refuse to send the actual control command unless the IPs match,
# and a potential attacker should have no way of knowing what IP they need to spoof.
# We also show the control sidebar panel when running in local dev mode.
@app.callback([Output('control-panel-footer', 'children'), Output('control-sidebar', 'hidden')],
              [Input('interval-component-fast', 'n_intervals')])
def update_control_sidebar(n):
    auth_reason = False

    if ip_matches():
        auth_reason = "IP Match"
    elif "mode" in os.environ and str(os.environ.get("mode")) == "dev":
        auth_reason = "Dev Mode"

    if auth_reason:
        return ["Control Authorized (" + auth_reason + ")"], False

    return [], True


# This helper function returns true if the client IP matches the IP of the photon that is stored in Redis
def ip_matches():
    # See here about getting the client IP that connects to Heroku: https://stackoverflow.com/a/37061471
    client_ip = request.remote_addr  # For local development
    if 'X-Forwarded-For' in request.headers:
        proxy_data = request.headers['X-Forwarded-For']
        ip_list = proxy_data.split(',')
        client_ip = ip_list[0]  # first address in list is User IP

    if r.exists('bike_ip') and client_ip == r.get('bike_ip'):
        return True

    return False


# This callback triggers when a resistance control button is clicked
@app.callback([Output({'type': 'resistance', 'index': 'down'}, 'disabled'),
               Output({'type': 'resistance', 'index': 'up'}, 'disabled'),
               Output('resistance-status', 'children'),
               Output('resistance-status', 'is_open'),
               Output('resistance-status', 'color')],
              [Input({'type': 'resistance', 'index': ALL}, 'n_clicks')])
def change_resistance(n_clicks):
    changed_id = [p['prop_id'] for p in dash.callback_context.triggered][0]
    try:
        index =  json.loads(re.search(r'(\{.*?\})', changed_id).group(1))['index']
    except AttributeError as e:
        raise PreventUpdate

    if index == 'down' or index == 'up':
        # As described in the update_control_sidebar() comments, we check the IP again and only send the command if there is a match.
        prefix = ""
        if not ip_matches():
            if "mode" in os.environ and str(os.environ.get("mode")) == "dev":
                prefix = "[Dev Override] "
            else:
                return False, False, "Command blocked due to IP mismatch.", True, 'warning'

        success, msg, data = particle_cloud_function('resistance_' + index)
        if success:
            res = int(data["return_value"])
            alert_text = prefix + "Resistance set to " + str(res)
            if res == MIN_RESISTANCE:
                return True, False, alert_text + " (Min)", True, 'success'
            elif res == MAX_RESISTANCE:
                return False, True, alert_text + " (Max)", True, 'success'
            else:
                return False, False, alert_text, True, 'success'
        else:
            alert_text = prefix + msg
            return False, False, alert_text, True, "primary"

    raise PreventUpdate


# This helper function sends a request to the particle cloud using the auth token and photon ID that should be stored in env vars.
# The command input is the name of the photon function to execute
# Returns a tuple with a boolean indicating success/failure, a status message, and the reply contents
def particle_cloud_function(cmd):
    address = 'https://api.particle.io/v1/devices/{}/{}'.format(os.environ.get("PARTICLE_ID"), cmd)
    data = {'access_token': os.environ.get("PARTICLE_TOKEN"), 'arg': ''}

    success = False
    msg = "An unknown failure occurred."

    req = requests.post(address, data=data)
    returned_data = req.json()
    if req.status_code == 200:
        success = True
        msg = "Command sent successfully."
        print('OK - Your Request was successfully delivered to the device and executed.')
    elif req.status_code == 400:
        success = False
        msg = "Command Failed! Is the bike on?"
        print('Bad Request - Your request is not understood by the device, or the requested subresource has not been exposed.')
    elif req.status_code == 401:
        success = False
        msg = "Control not Authorized!"
        print('Unauthorized - Your access token is not valid.')
    elif req.status_code == 403:
        success = False
        msg = "Control not Authorized for this Device!"
        print('Forbidden - Your access token is not authorized to interface with this device.')
    elif req.status_code == 404:
        success = False
        msg = "Device not available!"
        print('Not Found - The device you requested is not currently connected to the Particle cloud.')
    elif req.status_code == 408:
        success = False
        msg = "Command timed out!"
        print('Timed Out - The Particle cloud experienced a significant delay when trying to reach the device.')
    elif req.status_code == 429:
        success = False
        msg = "Command speed limit exceeded!"
        print('Too Many Requests - You are either making requests too often or too many at the same time.')
    elif req.status_code == 500:
        success = False
        msg = "Server error encountered!"
        print('Server error. Something is wrong with the Particle Cloud.')

    return success, msg, returned_data


# This callback triggers on an interval to update the text into the sidebar using the latest data in redis
@app.callback([Output('live-update-body', 'children'), Output('live-update-footer', 'children')],
              [Input('interval-component-fast', 'n_intervals')])
def update_metrics(n):

    if r.exists('session_end') and r.exists('timestamp'):
        start_datetime = datetime.datetime.fromtimestamp(int(r.get('session_start')))
        end_datetime = datetime.datetime.fromtimestamp(int(r.get('session_end')))
        speed_readings = [float(i) for i in r.lrange('bike_mph', 0, -1)]
        resistance_readings = [int(i) for i in r.lrange('resistance', 0, -1)]
        heart_readings = [float(i) for i in r.lrange('heart_bpm', 0, -1)]
        if len(speed_readings) > 0 and len(heart_readings) > 0:
            return [
                html.H5('Last Session Duration: {}'.format(date.delta(start_datetime, end_datetime)[0]), className='card-text'),
                html.Br(),
                html.P('Session Average Bike Speed: {0:0.2f} MPH'.format(sum(speed_readings)/len(speed_readings)), className='card-text'),
                html.P('Session Max Bike Speed: {0:0.2f} MPH'.format(max(speed_readings)), className='card-text'),
                html.Br(),
                html.P('Session Average Resistance: {0:0.2f}'.format(sum(resistance_readings)/len(resistance_readings)), className='card-text'),
                html.P('Session Max Resistance: {:d}'.format(max(resistance_readings)), className='card-text'),
                html.Br(),
                html.P('Session Average Heart Rate: {0:0.2f} BPM'.format(sum(heart_readings)/len(heart_readings)), className='card-text'),
                html.P('Session Max Heart Rate: {0:0.2f} BPM'.format(max(heart_readings)), className='card-text')
            ], 'Last session ended: {}'.format(date.duration(end_datetime))
    elif r.exists('session_start') and r.exists('timestamp'):
        start_datetime = datetime.datetime.fromtimestamp(int(r.get('session_start')))
        return [
            html.H5('Current session started: {}'.format(date.duration(start_datetime)), className='card-title'),
            html.P('Current Bike Speed: {0:0.2f} MPH'.format(float(r.lindex('bike_mph', 0))), className='card-text'),
            html.P('Current Resistance: {:d}'.format(int(r.lindex('resistance', 0))), className='card-text'),
            html.P('Current Heart Rate: {0:0.2f} BPM'.format(float(r.lindex('heart_bpm', 0))), className='card-text'),
        ], 'Last Update: {}'.format(datetime.datetime.fromtimestamp(int(r.lindex('timestamp', 0))).strftime('%c'))
    return [html.P('Waiting to receive data from bike...', className='card-text', style={'fontStyle': 'italic'})], [""]


# This callback fires on an interval to update the live graphs with the latest data from redis.
@app.callback([Output('live-update-graph', 'figure'), Output('graph-spinner', 'style'), Output('live-graph-div', 'style')],
              [Input('interval-component-slow', 'n_intervals')])
def update_graph_live(n):
    # Create the graph with subplots
    fig = make_subplots(rows=3, cols=1, vertical_spacing=0.1, subplot_titles=("Bike Speed", "Resistance", "Heart Rate"))
    fig.update_layout(
        xaxis=dict(
            fixedrange=True,
            title_font=dict(
                size=16,
                color='#839496',
            ),
            title_text="Time",
            zeroline=False,
            showline=False,
            showgrid=True,
            showticklabels=True,
            gridcolor='#839496',
            ticks='outside',
            tickfont=dict(
                size=14,
                color='#839496',
            ),
        ),
        xaxis2=dict(
            fixedrange=True,
            title_font=dict(
                size=16,
                color='#839496',
            ),
            title_text="Time",
            zeroline=False,
            showline=False,
            showgrid=True,
            showticklabels=True,
            gridcolor='#839496',
            ticks='outside',
            tickfont=dict(
                size=14,
                color='#839496',
            ),
        ),
        xaxis3=dict(
            fixedrange=True,
            title_font=dict(
                size=16,
                color='#839496',
            ),
            title_text="Time",
            zeroline=False,
            showline=False,
            showgrid=True,
            showticklabels=True,
            gridcolor='#839496',
            ticks='outside',
            tickfont=dict(
                size=14,
                color='#839496',
            ),
        ),
        yaxis=dict(
            fixedrange=True,
            range=[0, 35],
            title_font=dict(
                size=16,
                color='#839496',
            ),
            title_text="Speed (mph)",
            zeroline=False,
            rangemode='nonnegative',
            showline=False,
            showgrid=True,
            showticklabels=True,
            gridcolor='#839496',
            ticks='outside',
            tickfont=dict(
                size=14,
                color='#839496',
            ),
        ),
        yaxis2=dict(
            fixedrange=True,
            range=[0, MAX_RESISTANCE],
            title_font=dict(
                size=16,
                color='#839496',
            ),
            title_text="Resistance (" + str(MIN_RESISTANCE) + "-" + str(MAX_RESISTANCE) + ")",
            zeroline=False,
            rangemode='nonnegative',
            showline=False,
            showgrid=True,
            showticklabels=True,
            gridcolor='#839496',
            ticks='outside',
            tickfont=dict(
                size=14,
                color='#839496',
            ),
        ),
        yaxis3=dict(
            fixedrange=True,
            range=[0, 200],
            title_font=dict(
                size=16,
                color='#839496',
            ),
            title_text="Heart Rate (bpm)",
            zeroline=False,
            rangemode='nonnegative',
            showline=False,
            showgrid=True,
            showticklabels=True,
            gridcolor='#839496',
            ticks='outside',
            tickfont=dict(
                size=14,
                color='#839496',
            ),
        ),
        height=1100,
        autosize=True,
        margin=dict(
            autoexpand=True,
            l=30,
            r=30,
            b=30,
            t=30,
        ),
        showlegend=False,
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)'
    )

    for i in fig['layout']['annotations']:
        i['font'] = dict(size=20, color='#839496')

    if r.exists('timestamp'):
        data = {
            'timestamp': [datetime.datetime.fromtimestamp(int(x)) for x in r.lrange('timestamp', 0, -1)],
            'speed': [float(i) for i in r.lrange('bike_mph', 0, -1)],
            'resistance': [int(i) for i in r.lrange('resistance', 0, -1)],
            'heartrate': [float(i) for i in r.lrange('heart_bpm', 0, -1)]
        }
        fig.append_trace({
            'x': data['timestamp'],
            'y': data['speed'],
            'text': data['speed'],
            'name': 'Bike Speed',
            'mode': 'lines+markers',
            'type': 'scatter',
            'line': dict(color='#268BD2', width=2),
            'marker': dict(color='#268BD2', size=6),
        }, 1, 1)
        fig.append_trace({
            'x': data['timestamp'],
            'y': data['resistance'],
            'text': data['resistance'],
            'name': 'Resistance',
            'mode': 'lines+markers',
            'type': 'scatter',
            'line': dict(color='#2aa198', width=2),
            'marker': dict(color='#2aa198', size=6),
        }, 2, 1)
        fig.append_trace({
            'x': data['timestamp'],
            'y': data['heartrate'],
            'text': data['heartrate'],
            'name': 'Heart Rate',
            'mode': 'lines+markers',
            'type': 'scatter',
            'line': dict(color='#fd7e14', width=2),
            'marker': dict(color='#fd7e14', size=6),
        }, 3, 1)

    return fig, {'display': 'none'}, {'visibility': 'visible'}


if __name__ == '__main__':
    if "mode" in os.environ and str(os.environ.get("mode")) == "dev":
        app.run_server(host='127.0.0.1', debug=True, port=8050)
    else:
        app.run_server(host='0.0.0.0')
