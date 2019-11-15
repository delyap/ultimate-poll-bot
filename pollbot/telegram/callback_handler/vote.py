"""Callback functions needed during creation of a Poll."""
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from pollbot.i18n import i18n
from pollbot.helper import poll_allows_cumulative_votes
from pollbot.helper.stats import increase_stat
from pollbot.helper.enums import PollType, CallbackResult
from pollbot.helper.update import update_poll_messages

from pollbot.models import PollOption, Vote


def handle_vote(session, context):
    """Handle any clicks on vote buttons."""
    # Remove the poll, in case it got deleted, but we didn't manage to kill all references
    option = session.query(PollOption).get(context.payload)
    if option is None:
        if context.query.message is not None:
            context.query.message.edit_text(i18n.t('deleted.polls', locale=context.user.locale))
        else:
            context.bot.edit_message_text(
                i18n.t('deleted.polls', locale=context.user.locale),
                inline_message_id=context.query.inline_message_id,
            )
        return

    poll = option.poll
    try:
        # Single vote
        if poll.poll_type == PollType.single_vote.name:
            update_poll = handle_single_vote(session, context, option)
        # Block vote
        elif poll.poll_type == PollType.block_vote.name:
            update_poll = handle_block_vote(session, context, option)
        # Limited vote
        elif poll.poll_type == PollType.limited_vote.name:
            update_poll = handle_limited_vote(session, context, option)
        # Cumulative vote
        elif poll.poll_type == PollType.cumulative_vote.name:
            update_poll = handle_cumulative_vote(session, context, option)
        elif poll.poll_type == PollType.count_vote.name:
            update_poll = handle_cumulative_vote(session, context, option, unlimited=True)
        elif poll.poll_type == PollType.doodle.name:
            update_poll = handle_doodle_vote(session, context, option)
        elif poll.poll_type == PollType.single_transferable_vote.name:
            update_poll = handle_ordered_vote(session, context, option)
        else:
            raise Exception("Unknown poll type")
    except IntegrityError:
        # Double vote. Rollback the transaction and ignore the second vote
        session.rollback()
        return

    session.commit()
    if update_poll:
        update_poll_messages(session, context.bot, poll)

    increase_stat(session, 'votes')


def respond_to_vote(session, line, context, poll, remaining_votes=None, limited=False):
    """Get the formatted response for a user."""
    locale = poll.locale
    votes = session.query(Vote) \
        .filter(Vote.user == context.user) \
        .filter(Vote.poll == poll) \
        .all()

    if limited:
        line += i18n.t('callback.vote.votes_left', locale=locale, count=remaining_votes)

    lines = [line]
    lines.append(i18n.t('callback.vote.your_votes', locale=locale))
    for vote in votes:
        if poll_allows_cumulative_votes(poll):
            lines.append(f' {vote.poll_option.name} ({vote.vote_count}), ')
        else:
            lines.append(f' {vote.poll_option.name}')

    message = ''.join(lines)

    # Inline query responses cannot be longer than 200 characters
    # Restrict it, since we get an MessageTooLong error otherwise
    if len(message) > 190:
        message = message[0:190]

    context.query.answer(message)


def handle_single_vote(session, context, option):
    """Handle a single vote."""
    locale = option.poll.locale
    existing_vote = session.query(Vote) \
        .filter(Vote.poll == option.poll) \
        .filter(Vote.user == context.user) \
        .one_or_none()

    # Changed vote
    if existing_vote and existing_vote.poll_option != option:
        existing_vote.poll_option = option
        vote_changed = i18n.t('callback.vote.changed', locale=locale)
        respond_to_vote(session, vote_changed, context, option.poll)

    # Voted for the same thing again
    elif existing_vote and existing_vote.poll_option == option:
        session.delete(existing_vote)
        vote_removed = i18n.t('callback.vote.removed', locale=locale)
        context.query.answer(vote_removed)

    # First vote on this poll
    elif existing_vote is None:
        vote = Vote(context.user, option)
        session.add(vote)
        vote_registered = i18n.t('callback.vote.registered', locale=locale)
        respond_to_vote(session, vote_registered, context, option.poll)

    return True


def handle_block_vote(session, context, option):
    """Handle a block vote."""
    locale = option.poll.locale
    existing_vote = session.query(Vote) \
        .filter(Vote.poll_option == option) \
        .filter(Vote.user == context.user) \
        .one_or_none()

    # Remove vote
    if existing_vote:
        session.delete(existing_vote)
        vote_removed = i18n.t('callback.vote.removed', locale=locale)
        respond_to_vote(session, vote_removed, context, option.poll)

    # Add vote
    elif existing_vote is None:
        vote = Vote(context.user, option)
        session.add(vote)
        vote_registered = i18n.t('callback.vote.registered', locale=locale)
        respond_to_vote(session, vote_registered, context, option.poll)

    return True


def handle_limited_vote(session, context, option):
    """Handle a limited vote."""
    locale = option.poll.locale
    existing_vote = session.query(Vote) \
        .filter(Vote.poll_option == option) \
        .filter(Vote.user == context.user) \
        .one_or_none()

    vote_count = session.query(Vote) \
        .filter(Vote.poll == option.poll) \
        .filter(Vote.user == context.user) \
        .count()

    # Remove vote
    if existing_vote:
        session.delete(existing_vote)
        vote_removed = i18n.t('callback.vote.removed', locale=locale)
        respond_to_vote(session, vote_removed, context, option.poll, vote_count - 1, True)

    # Add vote
    elif existing_vote is None and vote_count < option.poll.number_of_votes:
        vote = Vote(context.user, option)
        session.add(vote)
        vote_registered = i18n.t('callback.vote.registered', locale=locale)
        respond_to_vote(session, vote_registered, context, option.poll, vote_count + 1, True)

    # Max votes reached
    else:
        no_left = i18n.t('callback.vote.no_left', locale=locale)
        respond_to_vote(session, no_left, context, option.poll)
        return False

    return True


def handle_cumulative_vote(session, context, option, unlimited=False):
    """Handle a cumulative vote."""
    locale = option.poll.locale
    existing_vote = session.query(Vote) \
        .filter(Vote.poll_option == option) \
        .filter(Vote.user == context.user) \
        .one_or_none()

    vote_count = session.query(func.sum(Vote.vote_count)) \
        .filter(Vote.poll == option.poll) \
        .filter(Vote.user == context.user) \
        .one()
    vote_count = vote_count[0]
    if vote_count is None:
        vote_count = 0

    action = context.callback_result
    allowed_votes = 10000000
    if not unlimited:
        allowed_votes = option.poll.number_of_votes

    # Upvote, but no votes left
    if not unlimited and action == CallbackResult.yes and vote_count >= allowed_votes:
        no_left = i18n.t('callback.vote.no_left', locale=locale)
        respond_to_vote(session, no_left, context, option.poll)
        return False

    # Early return if downvote on non existing vote
    if existing_vote is None and action == CallbackResult.no:
        respond_to_vote(session, 'Cannot downvote this option.', context, option.poll)
        return False

    if existing_vote:
        # Add to an existing vote
        if action == CallbackResult.yes:
            existing_vote.vote_count += 1
            session.commit()
            remaining_votes = allowed_votes - (vote_count + 1)
            vote_registered = i18n.t('callback.vote.registered', locale=locale)
            respond_to_vote(session, vote_registered, context, option.poll, remaining_votes, not unlimited)

        # Remove from existing vote
        elif action == CallbackResult.no:
            existing_vote.vote_count -= 1
            session.commit()
            remaining_votes = allowed_votes - (vote_count - 1)
            vote_removed = i18n.t('callback.vote.removed', locale=locale)
            respond_to_vote(session, vote_removed, context, option.poll, remaining_votes, not unlimited)

        # Delete vote if necessary
        if existing_vote.vote_count <= 0:
            session.delete(existing_vote)
            session.commit()

    # Add new vote
    elif existing_vote is None and action == CallbackResult.yes:
        vote = Vote(context.user, option)
        session.add(vote)
        session.commit()
        remaining_votes = allowed_votes - (vote_count + 1)
        vote_registered = i18n.t('callback.vote.registered', locale=locale)
        respond_to_vote(session, vote_registered, context, option.poll, remaining_votes, not unlimited)

    return True


def handle_doodle_vote(session, context, option):
    """Handle a doodle vote."""
    locale = option.poll.locale
    vote = session.query(Vote) \
        .filter(Vote.poll_option == option) \
        .filter(Vote.user == context.user) \
        .one_or_none()

    if context.callback_result.name is None:
        data = context.data # noqa
        raise Exception("Unknown callback result")

    # Remove vote
    if vote is not None:
        vote.type = context.callback_result.name
        changed = i18n.t('callback.vote.doodle_changed', locale=locale, vote_type=vote.type)
        context.query.answer(changed)

    # Add vote
    else:
        vote = Vote(context.user, option)
        vote.type = context.callback_result.name
        session.add(vote)
        registered = i18n.t('callback.vote.doodle_registered', locale=locale, vote_type=vote.type)
        context.query.answer(registered)

    return True

def handle_ordered_vote(session, context, option):
    vote = session.query(Vote) \
        .filter(Vote.poll_option == option) \
        .filter(Vote.user == context.user) \
        .one()

    if context.callback_result.name is None:
        data = context.data # noqa
        raise Exception("Unknown callback result")

    if context.callback_result.name == CallbackResult.increase_priority.name:
        direction = -1
    else:
        direction = 1

    next_vote = session.query(Vote) \
        .filter(Vote.user == context.user) \
        .filter(Vote.poll == vote.poll) \
        .filter(Vote.priority == vote.priority + direction) \
        .one()
    vote.priority += direction
    next_vote.priority -= direction

    registered = i18n.t('callback.vote.registered', locale=option.poll.locale)
    context.query.answer(registered)

    return True
