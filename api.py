#!/usr/bin/env python
from flask import Flask
from flask_restful import Resource, Api
from poll_api.resources.poll import PollResource

app = Flask(__name__)
api = Api(app)

api.add_resource(PollResource, '/')

if __name__ == '__main__':
    app.run(debug=True)
