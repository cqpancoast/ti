# coding: utf-8

"""
ti is a simple and extensible time tracker for the command line. Visit the
project page (http://ti.sharats.me) for more details.

Usage:
  ti (o|on) <name> [<time>...]
  ti (f|fin) [<time>...]
  ti (s|status)
  ti (t|tag) <tag>...
  ti (n|note) <note-text>...
  ti (l|log) [today]
  ti (e|edit)
  ti (i|interrupt)
  ti --no-color
  ti -h | --help

Options:
  -h --help         Show this help.
  <start-time>...   A time specification (goto http://ti.sharats.me for more on
                    this).
  <tag>...          Tags can be made of any characters, but its probably a good
                    idea to avoid whitespace.
  <note-text>...    Some arbitrary text to be added as `notes` to the currently
                    working project.
"""

from __future__ import print_function
from __future__ import unicode_literals

import json
import os
import re
import subprocess
import sys
import tempfile
import pytz
from datetime import datetime, timedelta, time
from collections import defaultdict
from os import path

import yaml
from colorama import Fore

# MY SHIT
tz = pytz.timezone("America/Los_Angeles")

class TIError(Exception):
    """Errors raised by TI."""


class AlreadyOn(TIError):
    """Already working on that task."""


class NoEditor(TIError):
    """No $EDITOR set."""


class InvalidYAML(TIError):
    """No $EDITOR set."""


class NoTask(TIError):
    """Not working on a task yet."""


class BadTime(TIError):
    """Time string can't be parsed."""


class BadArguments(TIError):
    """The command line arguments passed are not valid."""


class JsonStore(object):

    def __init__(self, filename):
        self.filename = filename

    def load(self):

        if path.exists(self.filename):
            with open(self.filename) as f:
                data = json.load(f)

        else:
            data = {'work': [], 'interrupt_stack': []}

        return data

    def dump(self, data):
        with open(self.filename, 'w') as f:
            json.dump(data, f, separators=(',', ': '), indent=2)


def red(str):
    if use_color:
        return Fore.RED + str + Fore.RESET
    else:
        return str


def green(str):
    if use_color:
        return Fore.GREEN + str + Fore.RESET
    else:
        return str


def yellow(str):
    if use_color:
        return Fore.YELLOW + str + Fore.RESET
    else:
        return str


def blue(str):
    if use_color:
        return Fore.BLUE + str + Fore.RESET
    else:
        return str


color_regex = re.compile("(\x9B|\x1B\\[)[0-?]*[ -\/]*[@-~]")


def strip_color(str):
    """Strip color from string."""
    return color_regex.sub("", str)


def len_color(str):
    """Compute how long the color escape sequences in the string are."""
    return len(str) - len(strip_color(str))


def ljust_with_color(str, n):
    """ljust string that might contain color."""
    return str.ljust(n + len_color(str))


def action_on(name, time):
    data = store.load()
    work = data['work']

    if work and 'end' not in work[-1]:
        raise AlreadyOn("You are already working on %s. Stop it or use a "
                        "different sheet." % (yellow(work[-1]['name']),))

    entry = {
        'name': name,
        'start': time,
    }

    work.append(entry)
    store.dump(data)

    print('Start working on ' + green(name) + '.')


def action_fin(time, back_from_interrupt=True):
    ensure_working()

    data = store.load()

    current = data['work'][-1]
    current['end'] = time
    store.dump(data)
    print('So you stopped working on ' + red(current['name']) + '.')

    if back_from_interrupt and len(data['interrupt_stack']) > 0:
        name = data['interrupt_stack'].pop()['name']
        store.dump(data)
        action_on(name, time)
        if len(data['interrupt_stack']) > 0:
            print('You are now %d deep in interrupts.'
                  % len(data['interrupt_stack']))
        else:
            print('Congrats, you\'re out of interrupts!')


def action_interrupt(name, time):
    ensure_working()

    action_fin(time, back_from_interrupt=False)

    data = store.load()
    if 'interrupt_stack' not in data:
        data['interrupt_stack'] = []
    interrupt_stack = data['interrupt_stack']

    interrupted = data['work'][-1]
    interrupt_stack.append(interrupted)
    store.dump(data)

    action_on('interrupt: ' + green(name), time)
    print('You are now %d deep in interrupts.' % len(interrupt_stack))


def action_note(content):
    ensure_working()

    data = store.load()
    current = data['work'][-1]

    if 'notes' not in current:
        current['notes'] = [content]
    else:
        current['notes'].append(content)

    store.dump(data)

    print('Yep, noted to ' + yellow(current['name']) + '.')


def action_tag(tags):
    ensure_working()

    data = store.load()
    current = data['work'][-1]

    current['tags'] = set(current.get('tags') or [])
    current['tags'].update(tags)
    current['tags'] = list(current['tags'])

    store.dump(data)

    tag_count = len(tags)
    print("Okay, tagged current work with %d tag%s."
          % (tag_count, "s" if tag_count > 1 else ""))


def action_status():
    ensure_working()

    data = store.load()
    current = data['work'][-1]

    start_time = parse_isotime(current['start'], tz)
    diff = timegap(start_time, datetime.now(tz))

    print('You have been working on {0} for {1}.'.format(
        green(current['name']), diff))


def action_log(period):
    data = store.load()
    work = data['work'] + data['interrupt_stack']
    log = defaultdict(lambda: {'delta': timedelta()})
    tag_log = defaultdict(lambda: {'delta': timedelta()})
    current = None

    def parse_time_string(time_string: str) -> tuple[datetime, datetime]:
        """
        Parse a time string and return a tuple of two datetime objects representing
        the start and end of the time period specified by the input string.

        The following input forms are allowed:
            - "today"
            - "yesterday"
            - "the last X days" (where X is an integer)
            - "X days ago" (where X is an integer)
            - "since Monday morning" (where Monday is the name of a day of the week)
            - "the past week"

        If the input string does not match one of these forms, a ValueError is raised.
        """
        
        if time_string is None:
            time_string = "today"

        now = datetime.now(tz)
        if time_string == "today":
            start = datetime.combine(now, time.min)
            end = datetime.combine(now, time.max)
        elif time_string == "yesterday":
            start = datetime.combine(now - timedelta(days=1), time.min)
            end = datetime.combine(now - timedelta(days=1), time.max)
        elif time_string.startswith("the last "):
            num_days = int(time_string.split()[-2])
            start = now - timedelta(days=num_days)
            end = datetime.combine(now, time.max)
        elif time_string.endswith(" ago"):
            num_days = int(time_string.split()[0])
            start = now - timedelta(days=num_days)
            end = now
        elif time_string == "this week":  # since monday
            day_of_week_num = datetime.now().weekday()
            start = datetime.combine(now - timedelta(days=day_of_week_num), time.min)
            end = datetime.combine(now, time.max)
        else:
            raise ValueError(f"Unrecognized time string: {time_string}")

        return start.astimezone(tz), end.astimezone(tz)

    start, end = parse_time_string(period)

    for item in work:
        start_time = parse_isotime(item['start'], tz)
        if start_time >= start and start_time < end:
            if 'end' in item:
                log[item['name']]['delta'] += (
                    parse_isotime(item['end'], tz) - start_time)
                if 'tags' in item:
                    for tag in item['tags']:
                        tag_log[tag]['delta'] += parse_isotime(item['end'], tz) - start_time
            else:
                log[item['name']]['delta'] += datetime.now(tz) - start_time
                if 'tags' in item:
                    for tag in item['tags']:
                        tag_log[tag]['delta'] += datetime.now(tz) - start_time
                current = item['name']

    name_col_len = 0

    print("Tasks:")
    for name, item in log.items():
        name_col_len = max(name_col_len, len(strip_color(name)))

        secs = item['delta'].total_seconds()
        tmsg = []

        if secs > 3600:
            hours = int(secs // 3600)
            secs -= hours * 3600
            tmsg.append(str(hours) + ' hour' + ('s' if hours > 1 else ''))

        if secs > 60:
            mins = int(secs // 60)
            secs -= mins * 60
            tmsg.append(str(mins) + ' minute' + ('s' if mins > 1 else ''))

        if secs:
            tmsg.append(str(int(secs)) + ' second' + ('s' if secs > 1 else ''))

        log[name]['tmsg'] = ', '.join(tmsg)[::-1].replace(',', '& ', 1)[::-1]

    for name, item in sorted(log.items(), key=(lambda x: x[0]), reverse=True):
        print("-", ljust_with_color(name, name_col_len), ' ∙∙ ', item['tmsg'],
              end=' ← working\n' if current == name else '\n')

    print("Tags:")
    for name, item in tag_log.items():
        name_col_len = max(name_col_len, len(strip_color(name)))

        secs = item['delta'].total_seconds()
        tmsg = []

        if secs > 3600:
            hours = int(secs // 3600)
            secs -= hours * 3600
            tmsg.append(str(hours) + ' hour' + ('s' if hours > 1 else ''))

        if secs > 60:
            mins = int(secs // 60)
            secs -= mins * 60
            tmsg.append(str(mins) + ' minute' + ('s' if mins > 1 else ''))

        if secs:
            tmsg.append(str(int(secs)) + ' second' + ('s' if secs > 1 else ''))

        tag_log[name]['tmsg'] = ', '.join(tmsg)[::-1].replace(',', '& ', 1)[::-1]

    for name, item in sorted(tag_log.items(), key=(lambda x: x[0]), reverse=True):
        print("-", ljust_with_color(name, name_col_len), ' ∙∙ ', item['tmsg'],
              end=' ← working\n' if current == name else '\n')

def action_edit():
    if "EDITOR" not in os.environ:
        raise NoEditor("Please set the 'EDITOR' environment variable")

    data = store.load()
    cmd = os.getenv('EDITOR')

    with tempfile.NamedTemporaryFile(mode='w+', delete=False) as temp:
        temp.write(json.dumps(data, indent=4))
        temp.seek(0)
        subprocess.call([cmd, temp.name])
        
    with open(temp.name) as temp:
        data = json.loads(temp.read())
    os.remove(temp.name)
    store.dump(data)


def is_working():
    data = store.load()
    return data.get('work') and 'end' not in data['work'][-1]


def ensure_working():
    if is_working():
        return

    raise NoTask("For all I know, you aren't working on anything. "
                 "I don't know what to do.\n"
                 "See `ti -h` to know how to start working.")


def to_datetime(timestr, tz):
    return parse_engtime(timestr, tz).isoformat() + 'Z'


def parse_engtime(timestr, tz):
    now = datetime.now(tz)
    if not timestr or timestr.strip() == 'now':
        return now

    match = re.match(r'(\d+|a) \s* (s|secs?|seconds?) \s+ ago $',
                     timestr, re.X)
    if match is not None:
        n = match.group(1)
        seconds = 1 if n == 'a' else int(n)
        return now - timedelta(seconds=seconds)

    match = re.match(r'(\d+|a) \s* (mins?|minutes?) \s+ ago $', timestr, re.X)
    if match is not None:
        n = match.group(1)
        minutes = 1 if n == 'a' else int(n)
        return now - timedelta(minutes=minutes)

    match = re.match(r'(\d+|a|an) \s* (hrs?|hours?) \s+ ago $', timestr, re.X)
    if match is not None:
        n = match.group(1)
        hours = 1 if n in ['a', 'an'] else int(n)
        return now - timedelta(hours=hours)

    raise BadTime("Don't understand the time %r" % (timestr,))


def parse_isotime(isotime, tz):
    return datetime.strptime(isotime, '%Y-%m-%dT%H:%M:%S.%f%zZ').astimezone(tz)


def timegap(start_time, end_time):
    diff = end_time - start_time

    mins = diff.total_seconds() // 60

    if mins == 0:
        return 'less than a minute'
    elif mins == 1:
        return 'a minute'
    elif mins < 44:
        return '{} minutes'.format(mins)
    elif mins < 89:
        return 'about an hour'
    elif mins < 1439:
        return 'about {} hours'.format(mins // 60)
    elif mins < 2519:
        return 'about a day'
    elif mins < 43199:
        return 'about {} days'.format(mins // 1440)
    elif mins < 86399:
        return 'about a month'
    elif mins < 525599:
        return 'about {} months'.format(mins // 43200)
    else:
        return 'more than a year'

def parse_args(argv=sys.argv):
    global use_color

    if '--no-color' in argv:
        use_color = False
        argv.remove('--no-color')

    # prog = argv[0]
    if len(argv) == 1:
        raise BadArguments("You must specify a command.")

    head = argv[1]
    tail = argv[2:]

    if head in ['-h', '--help', 'h', 'help']:
        raise BadArguments()

    elif head in ['e', 'edit']:
        fn = action_edit
        args = {}

    elif head in ['o', 'on']:
        if not tail:
            raise BadArguments("Need the name of whatever you are working on.")

        fn = action_on
        args = {
            'name': tail[0],
            'time': to_datetime(' '.join(tail[1:]), tz),
        }

    elif head in ['f', 'fin']:
        fn = action_fin
        args = {'time': to_datetime(' '.join(tail), tz)}

    elif head in ['s', 'status']:
        fn = action_status
        args = {}

    elif head in ['l', 'log']:
        fn = action_log
        args = {'period': tail[0] if tail else None}

    elif head in ['t', 'tag']:
        if not tail:
            raise BadArguments("Please provide at least one tag to add.")

        fn = action_tag
        args = {'tags': tail}

    elif head in ['n', 'note']:
        if not tail:
            raise BadArguments("Please provide some text to be noted.")

        fn = action_note
        args = {'content': ' '.join(tail)}

    elif head in ['i', 'interrupt']:
        if not tail:
            raise BadArguments("Need the name of whatever you are working on.")

        fn = action_interrupt
        args = {
            'name': tail[0],
            'time': to_datetime(' '.join(tail[1:]), tz),
        }

    else:
        raise BadArguments("I don't understand %r" % (head,))

    return fn, args


def main():
    try:
        fn, args = parse_args()
        fn(**args)
    except TIError as e:
        msg = str(e) if len(str(e)) > 0 else __doc__
        print(msg, file=sys.stderr)
        sys.exit(1)


store = JsonStore(os.getenv('SHEET_FILE', None) or
                  os.path.expanduser('~/.ti-sheet'))
use_color = True

if __name__ == '__main__':
    main()
