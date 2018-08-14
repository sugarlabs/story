# Copyright (C) 2008, Media Modifications Ltd.
# Copyright (C) 2011, One Laptop per Child

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import os

import gi
gi.require_version('Gst', '1.0')
from gi.repository import GObject, Gst

import logging

logger = logging.getLogger('arecord')


class Arecord:
    def __init__(self, activity_obj):
        logger.debug('__init__')
        self.activity = activity_obj

    def record_audio(self):
        logger.debug('record_audio')

        # make a pipeline to record and encode audio to file
        ogg = os.path.join(self.activity.datapath, "output.ogg")
        cmd = 'autoaudiosrc name=src ' \
            '! audioconvert ' \
            '! queue max-size-time=30000000000 ' \
            'max-size-bytes=0 max-size-buffers=0 ' \
            '! vorbisenc name=vorbis ! oggmux ' \
            '! filesink location=%s' % ogg
        self._audio = Gst.parse_launch(cmd)

        # detect end of stream
        bus = self._audio.get_bus()
        bus.add_signal_watch()

        def on_message_cb(bus, msg, ogg):
            if msg.type == Gst.MessageType.EOS:
                logger.debug('record_audio.on_message_cb Gst.MessageType.EOS')
                GObject.idle_add(self._stop_recording_audio, ogg)
                return

            if msg.type == Gst.MessageType.ERROR:
                err, debug = msg.parse_error()
                logger.error(
                    'record_audio.on_message_cb error=%s debug=%s' %
                    (err, debug))
                return

            if msg.type == Gst.MessageType.WARNING:
                err, debug = msg.parse_warning()
                logger.error(
                    'record_audio.on_message_cb warning=%s debug=%s' %
                    (err, debug))

        bus.connect('message', on_message_cb, ogg)

        # start audio pipeline recording
        self._audio.set_state(Gst.State.PLAYING)  # asynchronous

    def stop_recording_audio(self):
        logger.debug('stop_recording_audio')

        # ask for stream to end
        self._audio.get_by_name('src').send_event(Gst.Event.new_eos())

    def _stop_recording_audio(self, ogg):
        logger.debug('_stop_recording_audio')

        # note: caller is responsible for saving the audio file
        # output.ogg

        # remove the audio pipeline
        self._audio.get_bus().remove_signal_watch()
        self._audio.set_state(Gst.State.NULL)  # synchronous
        self._audio = None
        return False

    def is_complete(self):
        return self._audio is None
