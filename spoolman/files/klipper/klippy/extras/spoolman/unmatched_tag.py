"""What the printer says about a lane whose tag matched no Spoolman spool.

The tag itself was read fine, so the message always names what was read and what the user can
type next. "Spoolman has nothing for this tag" and "Spoolman never answered" are two different
problems with two different remedies, so they never wear the same sentence: reporting an
unreachable Spoolman as "no match" sends the user hunting for a spool that is already there.
"""
from .filament_info import filament_info_from_spoolman, filament_info_to_string

BIND_COMMAND = "SH_BIND_CARD_UID"
NO_CANDIDATES = "None of your Spoolman spools look like this tag"
CANDIDATE_HEADER = "Spools of yours that look like this tag"
CANDIDATES_UNSEARCHED = "Spoolman did not answer, so there is nothing to offer for the tag"


def _lane_tag_line(filament_info, level, problem, remedy):
    return f"{problem}: {filament_info_to_string(filament_info, level)}. {remedy}"


def unmatched_tag_message(channel, filament_info, level):
    return _lane_tag_line(
        filament_info, level,
        f"Nothing in Spoolman matches the tag on channel {channel}",
        f"Bind it to one of your spools with {BIND_COMMAND} CHANNEL={channel} SPOOL=<spool id>",
    )


# Every line names its channel, because four lanes report into one console and a line that does not
# say which lane it is about is a line the user cannot act on.
def _about_channel(sentence, channel):
    return f"{sentence} on channel {channel}"


# One line per spool, each carrying its own bind command, because a user at the machine copies the
# line they picked and types nothing else. A search that reached nobody says so rather than
# borrowing the wording of a search that came back empty.
def candidate_report(channel, candidates, level):
    if candidates is None:
        return [_about_channel(CANDIDATES_UNSEARCHED, channel)]
    if not candidates:
        return [_about_channel(NO_CANDIDATES, channel)]
    return [_about_channel(CANDIDATE_HEADER, channel) + ":"] + [
        _candidate_line(channel, spool, level) for spool in candidates
    ]


def _candidate_line(channel, spool, level):
    described = filament_info_to_string(filament_info_from_spoolman(spool), level)
    return f"  {described} -> {BIND_COMMAND} CHANNEL={channel} SPOOL={spool.get('id')}"


# Why a tag did not become a spool, as codes the client hands back, so the client never carries a
# sentence and this module stays the only place the console words live.
REGISTER_UNREACHABLE = "unreachable"
REGISTER_UNMEASURED_MATERIAL = "unmeasured-material"
REGISTER_REFUSED = "refused"
REGISTER_DISABLED = "disabled"

REGISTRATION_PROBLEMS = {
    REGISTER_UNREACHABLE:
        "Spoolman did not answer, so nothing was created for the tag on channel {channel}",
    REGISTER_REFUSED:
        "Spoolman would not take a new spool for the tag on channel {channel}",
    REGISTER_DISABLED:
        "Creating and updating spools from a tag is switched off, so the tag on channel {channel} "
        "was left alone (register_from_tag in [spoolman_helper])",
    REGISTER_UNMEASURED_MATERIAL:
        "You have no {material} in Spoolman to copy a diameter and a density from, so the tag on "
        "channel {channel} was not registered. Add one {material} spool in Spoolman, then "
        "re-insert this one",
}


def registered_spool_message(channel, spool_id):
    return f"The tag on channel {channel} is now Spoolman spool {spool_id}"


def applied_tag_message(channel, spool_id):
    return f"The tag on channel {channel} was written onto Spoolman spool {spool_id}"


def registration_problem_message(channel, problem, filament_info):
    material = (filament_info or {}).get("MAIN_TYPE") or "filament of that material"
    return REGISTRATION_PROBLEMS[problem].format(channel=channel, material=material)


def spoolman_silent_message(channel, filament_info, level):
    return _lane_tag_line(
        filament_info, level,
        f"Spoolman did not answer, so the tag on channel {channel} was not matched",
        "Check Spoolman is running, then re-insert the spool",
    )
