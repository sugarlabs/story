# Copyright (c) 2012-14 Walter Bender

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# You should have received a copy of the GNU General Public License
# along with this library; if not, write to the Free Software
# Foundation, 51 Franklin Street, Suite 500 Boston, MA 02110-1335 USA

import gi
gi.require_version('Gdk', '3.0')
gi.require_version('Gtk', '3.0')
from gi.repository import Gdk
from gi.repository import Gtk
from gi.repository import GObject
from gi.repository import Pango

import os
import subprocess
import random

from sugar3.activity import activity
from sugar3 import profile
from sugar3.datastore import datastore
from sugar3.activity.widgets import ActivityToolbarButton
from sugar3.activity.widgets import StopButton
from sugar3.graphics.toolbarbox import ToolbarBox
from sugar3.graphics.alert import Alert, ConfirmationAlert
from sugar3.graphics import style

from toolbar_utils import button_factory, separator_factory, radio_factory
from utils import json_load, json_dump
from aplay import aplay
from exportpdf import save_pdf
from arecord import Arecord

import telepathy
import dbus
from sugar3.presence import presenceservice

from collabwrapper import CollabWrapper

from gettext import gettext as _

from game import Game

import logging
_logger = logging.getLogger('story-activity')

PLACEHOLDER = _('Write your story here.')
PLACEHOLDER1 = _('Begin your story here.')
PLACEHOLDER2 = _('Continue your story here.')

SERVICE = 'org.sugarlabs.StoryActivity'
IFACE = SERVICE
PATH = '/org/sugarlabs/StoryActivity'


class StoryActivity(activity.Activity):
    ''' Storytelling game '''

    def __init__(self, handle):
        ''' Initialize the toolbars and the game board '''
        try:
            super(StoryActivity, self).__init__(handle)
        except dbus.exceptions.DBusException, e:
            _logger.error(str(e))

        self._path = activity.get_bundle_path()
        self.datapath = os.path.join(activity.get_activity_root(), 'instance')

        self._nick = profile.get_nick_name()
        if profile.get_color() is not None:
            self._colors = profile.get_color().to_string().split(',')
        else:
            self._colors = ['#A0FFA0', '#FF8080']

        self._old_cursor = self.get_window().get_cursor()

        self.tablet_mode = _is_tablet_mode()
        self.recording = False
        self.audio_process = None
        self._arecord = None
        self._alert = None
        self._uid = None

        self._setup_toolbars()
        self._setup_dispatch_table()

        self._fixed = Gtk.Fixed()
        self._fixed.connect('size-allocate', self._fixed_resize_cb)
        self._fixed.show()
        self.set_canvas(self._fixed)

        self._vbox = Gtk.VBox(False, 0)
        self._vbox.set_size_request(Gdk.Screen.width(), Gdk.Screen.height())

        self._fixed.put(self._vbox, 0, 0)
        self._vbox.show()

        self._canvas = Gtk.DrawingArea()
        self._canvas.set_size_request(int(Gdk.Screen.width()),
                                      int(Gdk.Screen.height()))
        self._canvas.show()

        self._vbox.pack_end(self._canvas, True, True, 0)
        self._vbox.show()

        entry_width = Gdk.Screen.width() - 7 * style.GRID_CELL_SIZE - \
            2 * style.DEFAULT_SPACING
        entry_height = 3 * style.GRID_CELL_SIZE - 2 * style.DEFAULT_SPACING
        self._entry = Gtk.TextView()
        self._entry.set_wrap_mode(Gtk.WrapMode.WORD)
        self._entry.set_pixels_above_lines(0)
        self._entry.set_size_request(entry_width, entry_height)
        font_desc = Pango.font_description_from_string('14')
        self._entry.modify_font(font_desc)
        self.text_buffer = self._entry.get_buffer()
        self.text_buffer.set_text(PLACEHOLDER)
        self._entry.connect('focus-in-event', self._text_focus_in_cb)
        self._entry.connect('key-press-event', self._text_focus_in_cb)
        self._entry.connect('focus-out-event', self._text_focus_out_cb)
        self._entry.get_buffer().connect('changed', self._text_changed_cb)

        self._entry.show()

        self._scrolled_window = Gtk.ScrolledWindow()
        self._scrolled_window.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
        self._scrolled_window.set_size_request(
            Gdk.Screen.width() - 6 * style.GRID_CELL_SIZE,
            style.GRID_CELL_SIZE * 3)
        self._scrolled_window.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        rgba = Gdk.RGBA()
        rgba.red, rgba.green, rgba.blue, rgba.alpha = 1., 1., 1., 1.
        self._scrolled_window.override_background_color(
            Gtk.StateFlags.NORMAL, rgba)

        self._scrolled_window.add(self._entry)

        if self.tablet_mode:
            self._fixed.put(self._scrolled_window, 3 * style.GRID_CELL_SIZE,
                            style.DEFAULT_SPACING)
        else:
            self._fixed.put(self._scrolled_window, 3 * style.GRID_CELL_SIZE,
                            Gdk.Screen.height() - style.DEFAULT_SPACING -
                            style.GRID_CELL_SIZE * 4)
        self._scrolled_window.show()
        self._fixed.show()

        self._game = Game(self._canvas, parent=self, path=self._path,
                          root=activity.get_bundle_path(), colors=self._colors)
        self._setup_presence_service()

        if 'mode' in self.metadata:
            self._game.set_mode(self.metadata['mode'])
            if self.metadata['mode'] == 'array':
                self.array_button.set_active(True)
                self.autoplay_button.set_sensitive(False)
            else:
                self._linear_button.set_active(True)

        if 'uid' in self.metadata:
            self._uid = self.metadata['uid']
        else:
            self._uid = generate_uid()
            self.metadata['uid'] = self._uid

        if 'dotlist' in self.metadata:
            self._restore()
            self.check_audio_status()
            self.check_text_status()
        else:
            self._game.new_game()

        Gdk.Screen.get_default().connect('size-changed', self._configure_cb)

    def close(self, **kwargs):
        aplay.close()
        activity.Activity.close(self, **kwargs)

    def _configure_cb(self, event):
        self._canvas.set_size_request(int(Gdk.Screen.width()),
                                      int(Gdk.Screen.height()))
        self._vbox.set_size_request(Gdk.Screen.width(), Gdk.Screen.height())
        entry_width = Gdk.Screen.width() - 7 * style.GRID_CELL_SIZE - \
            2 * style.DEFAULT_SPACING
        entry_height = 3 * style.GRID_CELL_SIZE - 2 * style.DEFAULT_SPACING
        self._entry.set_size_request(entry_width, entry_height)
        self._scrolled_window.set_size_request(
            Gdk.Screen.width() - 6 * style.GRID_CELL_SIZE,
            style.GRID_CELL_SIZE * 3)

        if not self.tablet_mode:
            self._fixed.move(
                self._scrolled_window, 3 * style.GRID_CELL_SIZE,
                Gdk.Screen.height() - style.DEFAULT_SPACING -
                style.GRID_CELL_SIZE * 4)

        self._game.configure()

    def _restore_cursor(self):
        ''' No longer waiting, so restore standard cursor. '''
        if not hasattr(self, 'get_window'):
            return
        self.get_window().set_cursor(self._old_cursor)

    def _waiting_cursor(self):
        ''' Waiting, so set watch cursor. '''
        if not hasattr(self, 'get_window'):
            return
        self._old_cursor = self.get_window().get_cursor()
        self.get_window().set_cursor(Gdk.Cursor.new(Gdk.CursorType.WATCH))

    def _text_changed_cb(self, text_buffer):
        self._entry.place_cursor_onscreen()

    def _fixed_resize_cb(self, widget=None, rect=None):
        ''' If a toolbar opens or closes, we need to resize the vbox
        holding out scrolling window. '''
        self._vbox.set_size_request(rect.width, rect.height)

    def _text_focus_in_cb(self, widget=None, event=None):
        bounds = self.text_buffer.get_bounds()
        text = self.text_buffer.get_text(bounds[0], bounds[1], True)
        if text in [PLACEHOLDER, PLACEHOLDER1, PLACEHOLDER2]:
            self.text_buffer.set_text('')
        self.metadata['dirty'] = 'True'
        self._game.set_speak_icon_state(True)
        if self._game.playing:
            self._game.stop()

    def _text_focus_out_cb(self, widget=None, event=None):
        self.speak_text_cb()

    def speak_text_cb(self, button=None):
        bounds = self.text_buffer.get_bounds()
        text = self.text_buffer.get_text(bounds[0], bounds[1], True)
        if self._game.get_mode() == 'array':
            if text != PLACEHOLDER:
                self.metadata['text'] = text
                self.metadata['dirty'] = 'True'
                self._game.set_speak_icon_state(True)
            else:
                self._game.set_speak_icon_state(False)
        else:
            if text not in [PLACEHOLDER, PLACEHOLDER1, PLACEHOLDER2]:
                key = 'text-%d' % self._game.current_image
                self.metadata[key] = text
                self.metadata['dirty'] = 'True'
                self._game.set_speak_icon_state(True)
            else:
                self._game.set_speak_icon_state(False)

    def check_text_status(self):
        self._game.set_speak_icon_state(False)
        if self._game.get_mode() == 'array':
            if 'text' in self.metadata:
                self.text_buffer.set_text(self.metadata['text'])
                if len(self.metadata['text']) > 0:
                    self.metadata['dirty'] = 'True'
                    self._game.set_speak_icon_state(True)
                else:
                    self.text_buffer.set_text(PLACEHOLDER)
            else:
                self.text_buffer.set_text(PLACEHOLDER)
        else:
            key = 'text-%d' % self._game.current_image
            if key in self.metadata:
                self.text_buffer.set_text(self.metadata[key])
                if len(self.metadata[key]) > 0:
                    self.metadata['dirty'] = 'True'
                    self._game.set_speak_icon_state(True)
                elif self._game.current_image == 0:
                    self.text_buffer.set_text(PLACEHOLDER1)
                else:
                    self.text_buffer.set_text(PLACEHOLDER2)
            elif self._game.current_image == 0:
                self.text_buffer.set_text(PLACEHOLDER1)
            else:
                self.text_buffer.set_text(PLACEHOLDER2)

    def _clear_text(self):
        if 'text' in self.metadata:
            self.metadata['text'] = ''
        for i in range(9):
            if 'text-%d' % i in self.metadata:
                self.metadata['text-%d' % i] = ''
        if 'dirty' in self.metadata:
            self.metadata['dirty'] = 'False'
        if self._game.get_mode() == 'array':
            self.text_buffer.set_text(PLACEHOLDER)
        elif self._game.current_image == 0:
            self.text_buffer.set_text(PLACEHOLDER1)
        else:
            self.text_buffer.set_text(PLACEHOLDER2)
        self._game.set_speak_icon_state(False)

    def _clear_audio_notes(self):
        dsobject = self._search_for_audio_note(self._uid, target=self._uid)
        if dsobject is not None:
            dsobject.metadata['tags'] = ''
            datastore.write(dsobject)
            dsobject.destroy()

        for i in range(9):
            target = '%s-%d' % (self._uid, i)
            dsobject = self._search_for_audio_note(self._uid, target=target)
            if dsobject is not None:
                dsobject.metadata['tags'] = ''
                datastore.write(dsobject)
                dsobject.destroy()
        self._game.set_play_icon_state(False)

    def check_audio_status(self):
        if self._search_for_audio_note(self._uid):
            self._game.set_play_icon_state(True)
        else:
            self._game.set_play_icon_state(False)

    def _setup_toolbars(self):
        ''' Setup the toolbars. '''

        self.max_participants = 4

        toolbox = ToolbarBox()

        # Activity toolbar
        activity_button = ActivityToolbarButton(self)

        toolbox.toolbar.insert(activity_button, 0)
        activity_button.show()

        self.set_toolbar_box(toolbox)
        toolbox.show()
        self.toolbar = toolbox.toolbar

        self._new_game_button_h = button_factory(
            'view-refresh', self.toolbar, self._new_game_cb,
            tooltip=_('Load new images'))

        self.array_button = radio_factory(
            'array', self.toolbar, self._array_cb,
            tooltip=_('View images all at once'), group=None)

        self._linear_button = radio_factory(
            'linear', self.toolbar, self._linear_cb,
            tooltip=_('View images one at a time'), group=self.array_button)

        self.autoplay_button = button_factory(
            'media-playback-start', self.toolbar, self._do_autoplay_cb,
            tooltip=_('Play'))
        self.autoplay_button.set_sensitive(False)

        separator_factory(self.toolbar)

        self.save_as_image = button_factory(
            'image-saveoff', self.toolbar, self._do_save_as_image_cb,
            tooltip=_('Save as image'))

        self.save_as_pdf = button_factory(
            'save-as-pdf', self.toolbar, self._do_save_as_pdf_cb,
            tooltip=_('Save as PDF'))

        separator_factory(toolbox.toolbar, True, False)

        stop_button = StopButton(self)
        stop_button.props.accelerator = '<Ctrl>q'
        toolbox.toolbar.insert(stop_button, -1)
        stop_button.show()

    def _do_autoplay_cb(self, button=None):
        if self._game.playing:
            self._game.stop()
        else:
            self.autoplay_button.set_icon_name('media-playback-pause')
            self.autoplay_button.set_tooltip(_('Pause'))
            self.array_button.set_sensitive(False)
            self._game.autoplay()

    def _array_cb(self, button=None):
        self.speak_text_cb()
        self._game.set_mode('array')
        self.autoplay_button.set_sensitive(False)
        if self._uid is not None:
            self.check_audio_status()
            self.check_text_status()

    def _linear_cb(self, button=None):
        self.speak_text_cb()
        self._game.set_mode('linear')
        self.autoplay_button.set_sensitive(True)
        if self._uid is not None:
            self.check_audio_status()
            self.check_text_status()

    def _new_game_cb(self, button=None):
        ''' Start a new game. '''
        if 'dirty' in self.metadata and self.metadata['dirty'] == 'True':
            if self._alert is not None:
                self.remove_alert(self._alert)
                self._alert = None
            self._alert = ConfirmationAlert()
            self._alert.props.title = \
                _('Do you really want to load new images?')
            self._alert.props.msg = _('You have done work on this story.'
                                      ' Do you want to overwrite it?')
            self._alert.connect('response', self._confirmation_alert_cb)
            self.add_alert(self._alert)
        else:
            self.autoplay_button.set_sensitive(False)
            self._game.new_game()

    def _confirmation_alert_cb(self, alert, response_id):
        self.remove_alert(alert)
        if response_id is Gtk.ResponseType.OK:
            self.autoplay_button.set_sensitive(False)
            self._clear_text()
            self._clear_audio_notes()
            self._game.new_game()

    def write_file(self, file_path):
        ''' Write the grid status to the Journal '''
        dot_list = self._game.save_game()
        self.metadata['dotlist'] = ''
        for dot in dot_list:
            self.metadata['dotlist'] += str(dot)
            if dot_list.index(dot) < len(dot_list) - 1:
                self.metadata['dotlist'] += ' '
        self.metadata['mode'] = self._game.get_mode()
        self.speak_text_cb()

    def _restore(self):
        ''' Restore the game state from metadata '''
        dot_list = []
        dots = self.metadata['dotlist'].split()
        for dot in dots:
            dot_list.append(int(dot))
        self._game.restore_game(dot_list)

    def _search_for_audio_note(self, obj_id, target=None):
        ''' Look to see if there is already a sound recorded for this
        dsobject: the object id is stored in a tag in the audio file. '''
        dsobjects, nobjects = datastore.find({'mime_type': ['audio/ogg']})
        # Look for tag that matches the target object id
        if target is None:
            if self._game.get_mode() == 'array':
                target = obj_id
            else:
                target = '%s-%d' % (obj_id, self._game.current_image)

        for dsobject in dsobjects:
            if 'tags' in dsobject.metadata and \
               target in dsobject.metadata['tags']:
                _logger.debug('Found audio note')
                self.metadata['dirty'] = 'True'
                return dsobject
        return None

    def _do_save_as_pdf_cb(self, button=None):
        self._waiting_cursor()
        self._notify_successful_save(title=_('Save as PDF'))
        GObject.idle_add(self._save_as_pdf)

    def _save_as_pdf(self):
        self.speak_text_cb()
        file_path = os.path.join(self.datapath, 'output.pdf')
        if 'description' in self.metadata:
            save_pdf(self, file_path, self._nick,
                     description=self.metadata['description'])
        else:
            save_pdf(self, file_path, self._nick)

        dsobject = datastore.create()
        dsobject.metadata['title'] = '%s %s' % \
            (self.metadata['title'], _('PDF'))
        dsobject.metadata['icon-color'] = profile.get_color().to_string()
        dsobject.metadata['mime_type'] = 'application/pdf'
        dsobject.metadata['activity'] = 'org.laptop.sugar3.ReadActivity'
        dsobject.set_file_path(file_path)
        datastore.write(dsobject)
        dsobject.destroy()
        os.remove(file_path)

        GObject.timeout_add(1000, self._remove_alert)

    def _do_save_as_image_cb(self, button=None):
        self._waiting_cursor()
        self._notify_successful_save(title=_('Save as image'))
        GObject.idle_add(self._save_as_image)

    def _save_as_image(self):
        ''' Grab the current canvas and save it to the Journal. '''
        if self._uid is None:
            self._uid = generate_uid()

        if self._game.get_mode() == 'array':
            target = self._uid
        else:
            target = '%s-%d' % (self._uid, self._game.current_image)

        file_path = os.path.join(self.datapath, 'story.png')
        png_surface = self._game.export()
        png_surface.write_to_png(file_path)

        dsobject = datastore.create()
        dsobject.metadata['title'] = '%s %s' % \
            (self.metadata['title'], _('image'))
        dsobject.metadata['icon-color'] = profile.get_color().to_string()
        dsobject.metadata['mime_type'] = 'image/png'
        dsobject.metadata['tags'] = target
        dsobject.set_file_path(file_path)
        datastore.write(dsobject)
        dsobject.destroy()
        os.remove(file_path)

        GObject.timeout_add(1000, self._remove_alert)

    def record_cb(self, button=None, cb=None):
        ''' Start/stop audio recording '''
        if self._arecord is None:
            self._arecord = Arecord(self)
        if self.recording:  # Was recording, so stop and later save
            self._game.set_record_icon_state(False)
            self._arecord.stop_recording_audio()
            self.recording = False
            self.busy()
            GObject.timeout_add(100, self._is_record_complete_timeout, cb)
        else:  # Wasn't recording, so start
            self._game.set_record_icon_state(True)
            self._arecord.record_audio()
            self.recording = True

    def _is_record_complete_timeout(self, cb=None):
        if not self._arecord.is_complete():
            return True  # call back later
        self._save_recording()
        self.unbusy()
        if cb is not None:
            cb()
        return False  # do not call back

    def playback_recording_cb(self, button=None):
        ''' Play back current recording '''
        if self.recording:  # Stop recording if we happen to be recording
            self.record_cb(cb=self._playback_recording)
        else:
            self._playback_recording()

    def _playback_recording(self):
        path = os.path.join(self.datapath, 'output.ogg')
        if self._uid is not None:
            dsobject = self._search_for_audio_note(self._uid)
            if dsobject is not None:
                path = dsobject.file_path
        aplay.play(path)

    def _save_recording(self):
        self.metadata['dirty'] = 'True'  # So we know that we've done work
        if os.path.exists(os.path.join(self.datapath, 'output.ogg')):
            _logger.debug('Saving recording to Journal...')
            if self._uid is None:
                self._uid = generate_uid()

            if self._game.get_mode() == 'array':
                target = self._uid
            else:
                target = '%s-%d' % (self._uid, self._game.current_image)

            dsobject = self._search_for_audio_note(target)
            if dsobject is None:
                dsobject = datastore.create()

            dsobject.metadata['title'] = \
                _('audio note for %s') % (self.metadata['title'])
            dsobject.metadata['icon-color'] = profile.get_color().to_string()
            dsobject.metadata['mime_type'] = 'audio/ogg'
            if self._uid is not None:
                dsobject.metadata['tags'] = target
            dsobject.set_file_path(os.path.join(self.datapath, 'output.ogg'))
            datastore.write(dsobject)
            dsobject.destroy()

            # Enable playback after record is finished
            self._game.set_play_icon_state(True)

            self.metadata['dirty'] = 'True'
            # Always save an image with the recording.
            # self._do_save_as_image_cb()
        else:
            _logger.debug('Nothing to save...')
        return

    def _notify_successful_save(self, title='', msg=''):
        ''' Notify user when saves are completed '''
        self._alert = Alert()
        self._alert.props.title = title
        self._alert.props.msg = msg
        self.add_alert(self._alert)
        self._alert.show()

    def _remove_alert(self):
        if self._alert is not None:
            self.remove_alert(self._alert)
            self._alert = None
        self._restore_cursor()

    # Collaboration-related methods

    def _setup_presence_service(self):
        ''' Setup the Presence Service. '''
        self.pservice = presenceservice.get_instance()
        self.initiating = None  # sharing (True) or joining (False)

        owner = self.pservice.get_owner()
        self.owner = owner
        self._share = ''
        self.connect('shared', self._shared_cb)
        self.connect('joined', self._joined_cb)

    def _shared_cb(self, activity):
        ''' Either set up initial share...'''
        self._new_tube_common(True)

    def _joined_cb(self, activity):
        ''' ...or join an exisiting share. '''
        self._new_tube_common(False)

    def _new_tube_common(self, sharer):
        ''' Joining and sharing are mostly the same... '''
        shared_activity = self.get_shared_activity()
        if shared_activity is None:
            _logger.error('Failed to share or join activity')
            return

        self.initiating = sharer
        self.waiting_for_hand = not sharer

        self.conn = shared_activity.telepathy_conn
        self.tubes_chan = shared_activity.telepathy_tubes_chan
        self.text_chan = shared_activity.telepathy_text_chan

        self.tubes_chan[telepathy.CHANNEL_TYPE_TUBES].connect_to_signal(
            'NewTube', self._new_tube_cb)

        if sharer:
            _logger.debug('This is my activity: making a tube...')
            self.tubes_chan[telepathy.CHANNEL_TYPE_TUBES].OfferDBusTube(
                SERVICE, {})
        else:
            _logger.debug('I am joining an activity: waiting for a tube...')
            self.tubes_chan[telepathy.CHANNEL_TYPE_TUBES].ListTubes(
                reply_handler=self._list_tubes_reply_cb,
                error_handler=self._list_tubes_error_cb)
        self._game.set_sharing(True)

    def _list_tubes_reply_cb(self, tubes):
        ''' Reply to a list request. '''
        for tube_info in tubes:
            self._new_tube_cb(*tube_info)

    def _list_tubes_error_cb(self, e):
        ''' Log errors. '''
        _logger.error('Error: ListTubes() failed: %s' % (e))

    def _new_tube_cb(self, id, initiator, type, service, params, state):
        ''' Create a new tube. '''
        _logger.debug('New tube: ID=%d initator=%d type=%d service=%s'
                      ' params=%r state=%d' %
                      (id, initiator, type, service, params, state))

        if (type == telepathy.TUBE_TYPE_DBUS and service == SERVICE):
            if state == telepathy.TUBE_STATE_LOCAL_PENDING:
                self.tubes_chan[
                    telepathy.CHANNEL_TYPE_TUBES].AcceptDBusTube(id)

            self.collab = CollabWrapper(self)
            self.collab.message.connect(self.event_received_cb)
            self.collab.setup()

    def _setup_dispatch_table(self):
        ''' Associate tokens with commands. '''
        self._processing_methods = {
            'n': [self._receive_new_images, 'get a new game grid'],
            'p': [self._receive_dot_click, 'get a dot click'],
        }

    def event_received_cb(self, collab, buddy, msg):
        ''' Data from a tube has arrived. '''
        command = msg.get("command")
        if command is None:
            return

        payload = msg.get("payload")
        self._processing_methods[command][0](payload)

    def send_new_images(self):
        ''' Send a new image grid to all players '''
        self.send_event("n", json_dump(self._game.save_game()))

    def _receive_new_images(self, payload):
        ''' Sharer can start a new game. '''
        dot_list = json_load(payload)
        self._game.restore_game(dot_list)

    def send_dot_click(self, dot, color):
        ''' Send a dot click to all the players '''
        self.send_event("p", json_dump([dot, color]))

    def _receive_dot_click(self, payload):
        ''' When a dot is clicked, everyone should change its color. '''
        (dot, color) = json_load(payload)
        self._game.remote_button_press(dot, color)

    def send_event(self, command, payload):
        ''' Send event through the tube. '''
        if hasattr(self, 'chattube') and self.collab is not None:
            self.collab.SendText(dict(
                command=command,
                payload=payload,
            ))


def generate_uid():
    left = '%04x' % int(random.uniform(0, int(0xFFFF)))
    right = '%04x' % int(random.uniform(0, int(0xFFFF)))
    uid = '%s-%s' % (left, right)
    return uid.upper()


def _is_tablet_mode():
    if not os.path.exists('/dev/input/event4'):
        return False
    try:
        output = subprocess.call(
            ['evtest', '--query', '/dev/input/event4', 'EV_SW',
             'SW_TABLET_MODE'])
    except (OSError, subprocess.CalledProcessError):
        return False
    if str(output) == '10':
        return True
    return False
