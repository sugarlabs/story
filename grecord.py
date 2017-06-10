#Copyright (c) 2008, Media Modifications Ltd.
#Copyright (c) 2011-13, Walter Bender

#Permission is hereby granted, free of charge, to any person obtaining a copy
#of this software and associated documentation files (the "Software"), to deal
#in the Software without restriction, including without limitation the rights
#to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#copies of the Software, and to permit persons to whom the Software is
#furnished to do so, subject to the following conditions:

#The above copyright notice and this permission notice shall be included in
#all copies or substantial portions of the Software.

#THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
#THE SOFTWARE.

import os
import time

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gtk, Gst

import logging
_logger = logging.getLogger("portfolio-activity")

from gi.repository import GObject
GObject.threads_init()
Gst.init(None)


class Grecord:

    def __init__(self, parent):
        self._activity = parent
        self._eos_cb = None

        self._can_limit_framerate = False
        self._playing = False

        self._audio_transcode_handler = None
        self._transcode_id = None

        self._pipeline = Gst.Pipeline()
        self._create_audiobin()

        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect('message', self._bus_message_handler)
        _logger.debug('Grecord.__init__ complete')

    def _create_audiobin(self):
        src = Gst.ElementFactory.make('alsasrc', None)

        # attempt to use direct access to the 0,0 device, solving some A/V
        # sync issues
        src.set_property("device", "plughw:0,0")
        hwdev_available = src.set_state(Gst.State.PAUSED) != \
                          Gst.StateChangeReturn.FAILURE
        src.set_state(Gst.State.NULL)
        if not hwdev_available:
            src.set_property("device", "default")

        src.set_state(Gst.State.NULL)
        src.set_property('device', 'default')

        srccaps = Gst.caps_from_string(
            'audio/x-raw,rate=(int)48000,channels=(int)1,depth=(int)16')

        # Guarantee perfect stream, important for A/V sync
        rate = Gst.ElementFactory.make('audiorate', None)

        # Without a buffer here, gstreamer struggles at the start of the
        # recording and then the A/V sync is bad for the whole video
        # (possibly a gstreamer/ALSA bug -- even if it gets caught up, it
        # should be able to resync without problem).
        queue = Gst.ElementFactory.make('queue', None)
        queue.set_property('leaky', True)  # prefer fresh data
        queue.set_property('max-size-time', 5000000000)  # 5 seconds
        queue.set_property('max-size-buffers', 500)
        queue.connect('overrun', self._log_queue_overrun)

        enc = Gst.ElementFactory.make('wavenc', None)

        sink = Gst.ElementFactory.make('filesink', None)
        _logger.debug(os.path.join(self._activity.datapath, 'output.wav'))
        sink.set_property('location',
            os.path.join(self._activity.datapath, 'output.wav'))

        self._audiobin = Gst.Bin()
        self._audiobin.add(src)
        self._audiobin.add(rate)
        self._audiobin.add(queue)
        self._audiobin.add(enc)
        self._audiobin.add(sink)

        src.link_filtered(rate, srccaps)
        rate.link(queue)
        queue.link(enc)
        enc.link(sink)
        _logger.debug('audio_bin complete')

    def _log_queue_overrun(self, queue):
        cbuffers = queue.get_property('current-level-buffers')
        cbytes = queue.get_property('current-level-bytes')
        ctime = queue.get_property('current-level-time')

    def play(self):
        if self._get_state() == Gst.State.PLAYING:
            return

        self._pipeline.set_state(Gst.State.PLAYING)
        self._playing = True

    def pause(self):
        self._pipeline.set_state(Gst.State.PAUSED)
        self._playing = False

    def stop(self):
        self._pipeline.set_state(Gst.State.NULL)
        self._playing = False

    def is_playing(self):
        return self._playing

    def _get_state(self):
        return self._pipeline.get_state(0)[1]

    def stop_recording_audio(self):
        # We should be able to simply pause and remove the audiobin, but
        # this seems to cause a gstreamer segfault. So we stop the whole
        # pipeline while manipulating it.
        # http://dev.laptop.org/ticket/10183
        self._pipeline.set_state(Gst.State.NULL)
        self._pipeline.remove(self._audiobin)
        self.play()

        audio_path = os.path.join(self._activity.datapath, 'output.wav')
        if not os.path.exists(audio_path) or os.path.getsize(audio_path) <= 0:
            # FIXME: inform model of failure?
            _logger.error('output.wav does not exist or is empty')
            return

        _logger.debug('stop_recording_audio')

        line = 'filesrc location=' + audio_path + ' name=audioFilesrc ! \
wavparse name=audioWavparse ! audioconvert name=audioAudioconvert ! \
vorbisenc name=audioVorbisenc ! oggmux name=audioOggmux ! \
filesink name=audioFilesink'
        self._audioline = Gst.parse_launch(line)

        vorbis_enc = self._audioline.get_by_name('audioVorbisenc')

        audioFilesink = self._audioline.get_by_name('audioFilesink')
        audioOggFilepath = os.path.join(self._activity.datapath, 'output.ogg')
        audioFilesink.set_property('location', audioOggFilepath)

        audioBus = self._audioline.get_bus()
        audioBus.add_signal_watch()
        self._audio_transcode_handler = audioBus.connect(
            'message::eos', self._onMuxedAudioMessageCb, self._audioline)
        self._transcode_id = GObject.timeout_add(200, self._transcodeUpdateCb,
                                                 self._audioline)
        self._audiopos = 0
        self._audioline.set_state(Gst.State.PLAYING)

    def transcoding_complete(self):
        # The EOS message is sometimes either not sent or not received.
        # So if the position in the stream is not advancing, assume EOS.
        _logger.debug('transcoding complete')
        if self._transcode_id is None:
            _logger.debug('EOS.... transcoding finished')
            return True
        else:
            position, duration = self._query_position(self._audioline)
            _logger.debug('position: %s, duration: %s' % (str(position),
                                                          str(duration)))
            if position == duration:
                _logger.debug('We are done, even though we did not see EOS')
                self._clean_up_transcoding_pipeline(self._audioline)
                return True
            elif position == self._audiopos:
                _logger.debug('No progess, so assume we are done')
                self._clean_up_transcoding_pipeline(self._audioline)
                return True
            self._audiopos = position
            return False

    def blockedCb(self, x, y, z):
        pass

    def record_audio(self):
        # We should be able to add the audiobin on the fly, but unfortunately
        # this results in several seconds of silence being added at the start
        # of the recording. So we stop the whole pipeline while adjusting it.
        # SL#2040
        _logger.debug('record audio')
        self._pipeline.set_state(Gst.State.NULL)
        self._pipeline.add(self._audiobin)
        self.play()

    def _transcodeUpdateCb(self, pipe):
        position, duration = self._query_position(pipe)
        if position != Gst.CLOCK_TIME_NONE:
            value = position * 100.0 / duration
            value = value / 100.0
        return True

    def _query_position(self, pipe):
        result, position = pipe.query_position(Gst.Format.TIME)
        if not result:
            position = Gst.CLOCK_TIME_NONE

        result, duration = pipe.query_duration(Gst.Format.TIME)
        if not result:
            duration = Gst.CLOCK_TIME_NONE

        return (position, duration)

    def _onMuxedAudioMessageCb(self, bus, message, pipe):
        # _logger.debug(message.type)
        if message.type != Gst.MessageType.EOS:
            return True
        self._clean_up_transcoding_pipeline(pipe)
        return False

    def _clean_up_transcoding_pipeline(self, pipe):
        GObject.source_remove(self._audio_transcode_handler)
        self._audio_transcode_handler = None
        GObject.source_remove(self._transcode_id)
        self._transcode_id = None
        pipe.set_state(Gst.State.NULL)
        pipe.get_bus().remove_signal_watch()
        pipe.get_bus().disable_sync_message_emission()

        wavFilepath = os.path.join(self._activity.datapath, 'output.wav')
        _logger.debug('skip clean up of output.wav')
        # os.remove(wavFilepath)
        return

    def _bus_message_handler(self, bus, message):
        t = message.type
        if t == Gst.MessageType.EOS:
            if self._eos_cb:
                cb = self._eos_cb
                self._eos_cb = None
                cb()
        elif t == Gst.MessageType.ERROR:
            # TODO: if we come out of suspend/resume with errors, then
            # get us back up and running...  TODO: handle 'No space
            # left on the resource.gstfilesink.c' err, debug =
            # message.parse_error()
            pass
