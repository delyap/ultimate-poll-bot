from flask_restful import Resource
from pollbot.models import Poll
from pollbot.db import get_session

class PollResource(Resource):
    def get(self):
        # print("debug")
        # return {'asd':'asd'}
        session = get_session()
        polls = session.query(Poll) \
            .filter(Poll.created.is_(True)) \
            .filter(Poll.closed.is_(False)) \
            .all()
            # .filter(Poll.user == user) TODO
        print(polls[0])
        return polls[0]
