# Copyright (c) 2011-14 Walter Bender

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# You should have received a copy of the GNU General Public License
# along with this library; if not, write to the Free Software
# Foundation, 51 Franklin Street, Suite 500 Boston, MA 02110-1335 USA

import os
from pipes import quote
from StringIO import StringIO

import json
json.dumps
from json import load as jload
from json import dump as jdump


def json_load(text):
    """ Load JSON data using what ever resources are available. """
    # strip out leading and trailing whitespace, nulls, and newlines
    io = StringIO(text)
    try:
        listdata = jload(io)
    except ValueError:
        # assume that text is ascii list
        listdata = text.split()
        for i, value in enumerate(listdata):
            listdata[i] = int(value)
    return listdata


def json_dump(data):
    """ Save data using available JSON tools. """
    _io = StringIO()
    jdump(data, _io)
    return _io.getvalue()
VOICES = {'af': 'afrikaans', 'cy': 'welsh-test', 'el': 'greek',
          'es': 'spanish', 'hi': 'hindi-test', 'hy': 'armenian',
          'ku': 'kurdish', 'mk': 'macedonian-test', 'pt': 'brazil',
          'sk': 'slovak', 'sw': 'swahili', 'bs': 'bosnian',
          'da': 'danish', 'en': 'english', 'fi': 'finnish',
          'hr': 'croatian', 'id': 'indonesian-test', 'la': 'latin',
          'nl': 'dutch-test', 'sq': 'albanian', 'ta': 'tamil',
          'vi': 'vietnam-test', 'ca': 'catalan', 'de': 'german',
          'eo': 'esperanto', 'fr': 'french', 'hu': 'hungarian',
          'is': 'icelandic-test', 'lv': 'latvian', 'no': 'norwegian',
          'ro': 'romanian', 'sr': 'serbian', 'zh': 'Mandarin',
          'cs': 'czech', 'it': 'italian', 'pl': 'polish',
          'ru': 'russian_test', 'sv': 'swedish', 'tr': 'turkish'}


def speak(text):
    """ Speak text """

    if type(text) == float and int(text) == text:
        text = int(text)
    safetext = '{}'.format(quote(str(text)))

    lang = os.environ['LANG'][0:2]
    if lang in VOICES:
        command = 'espeak -v %s "%s"' % (VOICES[lang], safetext)
    else:
        command = 'espeak "%s"' % (safetext)

    os.system('%s --stdout | aplay' % command)
