from flask_restful import Resource, fields, marshal_with
from pollbot.models import Poll
from pollbot.db import get_session

resource_fields = {
    'uuid': fields.String,
    'name': fields.String,
    'description': fields.String,
    'locale': fields.String,
    'poll_type': fields.String,
    'anonymous': fields.Boolean,
    'results_visible': fields.Boolean,
    'due_date': fields.DateTime,
    'created': fields.Boolean,
    'closed': fields.Boolean
}

class PollResource(Resource):
    @marshal_with(resource_fields)
    def get(self):
        session = get_session()
        polls = session.query(Poll) \
            .filter(Poll.created.is_(True)) \
            .filter(Poll.closed.is_(False)) \
            .all()
            # .filter(Poll.user == user) TODO
        print(polls)
        return polls
