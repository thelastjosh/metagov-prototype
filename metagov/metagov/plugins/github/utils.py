def create_issue_text(vote_type, parameters):

    question = parameters.pop("question")
    help_text = parameters.pop("help_text", None)
    help_text = f"Additional guidance: {help_text}\n\n" if help_text else ""
    max_votes = parameters.pop("max_votes", None)
    max_votes = f" This vote will end when there are {max_votes} total votes." if max_votes else ""

    title = f"VOTE: {question}"

    if vote_type == "react":

        body = (
            "This is a react vote issue. Please vote on the question below, using "
            "thumbs up for yes and thumbs down for no. Other reactions will not be counted."
            f"{max_votes}"
            "\n\n"
            f"{help_text}"
            f"QUESTION: {question}"
        )

    elif vote_type == "comment":

        body = (
            "This is a comment vote issue. Please vote on the question below by leaving a comment with "
            "the text `^^^^<yourvotehere>^^^^`. You can add any additional commentary you like. \n\n"
            "Please note that your vote will not be case sensitive, but spelling _does_ matter. If you try "
            "to vote multiple times, only your first vote will be counted. You may edit your vote as long as "
            "the vote is still open."
            f"{max_votes}"
            "\n\n"
            f"{help_text}"
            f"QUESTION: {question}"
        )

    return title, body


def close_react_vote_text(result, upvotes, downvotes):
    return (
        f"This vote is now closed. The result was {result} with {upvotes} votes for "
        f"and {downvotes} votes against. You may continue commenting and reacting, but it will "
        "have no impact on the result."
    )


def close_comment_vote_text(voter_list, votes):
    outcome = ""
    for name, count in votes.most_common():
        outcome += f"{name}: {count}\n"
    return(
        f"This vote is now closed. You may continue commenting, but it will not affect the result.\n\n"
        f"The result was:\n\n{outcome}\n"
        f"People voting: {', '.join(voter_list)}"
    )