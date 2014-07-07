#Copyright (c) 2012-14 Walter Bender

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# You should have received a copy of the GNU General Public License
# along with this library; if not, write to the Free Software
# Foundation, 51 Franklin Street, Suite 500 Boston, MA 02110-1335 USA


from gi.repository import Gdk
from gi.repository import Gtk
from gi.repository import GObject
from gi.repository import Pango

import os
import time

from sugar3.activity import activity
from sugar3 import profile
from sugar3.datastore import datastore
from sugar3.activity.widgets import ActivityToolbarButton
from sugar3.activity.widgets import StopButton
from sugar3.graphics.toolbarbox import ToolbarBox
from sugar3.graphics.alert import Alert, ConfirmationAlert
from sugar3.graphics import style

from toolbar_utils import button_factory, separator_factory, radio_factory
from utils import json_load, json_dump, play_audio_from_file
from grecord import Grecord

import telepathy
import dbus
from dbus.service import signal
from dbus.gobject_service import ExportedGObject
from sugar3.presence import presenceservice
from sugar3.presence.tubeconn import TubeConnection

from gettext import gettext as _

from game import Game

import logging
_logger = logging.getLogger('story-activity')

PLACEHOLDER = _('Write your story here.')
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

        self.path = activity.get_bundle_path()
        self.datapath = os.path.join(activity.get_activity_root(), 'instance')

        self.nick = profile.get_nick_name()
        if profile.get_color() is not None:
            self.colors = profile.get_color().to_string().split(',')
        else:
            self.colors = ['#A0FFA0', '#FF8080']

        self.recording = False
        self._grecord = None
        self._alert = None
        self._uid = None

        self._setup_toolbars()
        self._setup_dispatch_table()

        self.fixed = Gtk.Fixed()
        self.fixed.connect('size-allocate', self._fixed_resize_cb)
        self.fixed.show()
        self.set_canvas(self.fixed)

        self.vbox = Gtk.VBox(False, 0)
        self.vbox.set_size_request(Gdk.Screen.width(), Gdk.Screen.height())
        self.fixed.put(self.vbox, 0, 0)
        self.vbox.show()

        self._canvas = Gtk.DrawingArea()
        self._canvas.set_size_request(int(Gdk.Screen.width()),
                                      int(Gdk.Screen.height()))
        self._canvas.show()
        self.show_all()
        self.vbox.pack_end(self._canvas, True, True, 0)
        self.vbox.show()

        self.entry = Gtk.TextView()
        self.entry.set_wrap_mode(Gtk.WrapMode.WORD)
        self.entry.set_pixels_above_lines(0)
        self.entry.set_size_request(
            Gdk.Screen.width() - 5 * style.GRID_CELL_SIZE -
            2 * style.DEFAULT_SPACING,
            style.GRID_CELL_SIZE * 3 - 2 * style.DEFAULT_SPACING)
        font_desc = Pango.font_description_from_string('24')
        self.entry.modify_font(font_desc)
        self.text_buffer = self.entry.get_buffer() 
        self.text_buffer.set_text(PLACEHOLDER)
        self.entry.connect('focus-in-event', self._text_focus_in_cb)
        self.entry.connect('focus-out-event', self._text_focus_out_cb)

        grid = Gtk.Grid()
        grid.set_border_width(style.DEFAULT_PADDING)
        grid.attach(self.entry, 0, 0, 1, 1)
        self.entry.show()

        evbox = Gtk.EventBox()
        evbox.add(grid)
        grid.show()
        evbox.connect('focus-in-event', self._text_focus_in_cb)
        evbox.connect('focus-out-event', self._text_focus_out_cb)

        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
        scrolled_window.set_size_request(
            Gdk.Screen.width() - 5 * style.GRID_CELL_SIZE,
            style.GRID_CELL_SIZE * 3)
        scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.ALWAYS)
        vadj = scrolled_window.get_vadjustment()
        vadj.connect('changed', self._scroll_changed_cb)
        scrolled_window.add_with_viewport(evbox)
        evbox.show()

        self.fixed.put(scrolled_window, 2 * style.GRID_CELL_SIZE,
                       style.GRID_CELL_SIZE)
        scrolled_window.show()
        self.fixed.show()

        self._game = Game(self._canvas, parent=self, path=self.path,
                          root=activity.get_bundle_path(), colors=self.colors)
        self._setup_presence_service()

        if 'mode' in self.metadata:
            self._game.set_mode(self.metadata['mode'])
            if self.metadata['mode'] == 'array':
                self.array_button.set_active(True)
            else:
                self.linear_button.set_active(True)

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

    def _scroll_changed_cb(self, adj, scroll=None):
        '''Scroll the chat window to the bottom'''
        adj.set_value(adj.get_upper() - adj.get_page_size())

    def _fixed_resize_cb(self, widget=None, rect=None):
        ''' If a toolbar opens or closes, we need to resize the vbox
        holding out scrolling window. '''
        self.vbox.set_size_request(rect.width, rect.height)

    def _text_focus_in_cb(self, widget=None, event=None):
        bounds = self.text_buffer.get_bounds()
        text = self.text_buffer.get_text(bounds[0], bounds[1], True)
        if text == PLACEHOLDER or text == PLACEHOLDER2:
            self.text_buffer.set_text('')

    def _text_focus_out_cb(self, widget=None, event=None):
        self.save_text_cb()
    
    def save_text_cb(self, button=None):
        bounds = self.text_buffer.get_bounds()
        text = self.text_buffer.get_text(bounds[0], bounds[1], True)
        if self._game.get_mode() == 'array':
            if text != PLACEHOLDER:
                self.metadata['text'] = text
        else:
            if text != PLACEHOLDER and text != PLACEHOLDER2:
                key = 'text-%d' % self._game.current_image
                self.metadata[key] = text

    def check_text_status(self):
        if self._game.get_mode() == 'array':
            if 'text' in self.metadata:
                self.text_buffer.set_text(self.metadata['text'])
            else:
                self.text_buffer.set_text(PLACEHOLDER)
        else:
            key = 'text-%d' % self._game.current_image
            if key in self.metadata:
                self.text_buffer.set_text(self.metadata[key])
            elif self._game.current_image == 0:
                self.text_buffer.set_text(PLACEHOLDER)
            else:
                self.text_buffer.set_text(PLACEHOLDER2)

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
            tooltip=_('Load new images.'))

        self.array_button = radio_factory(
            'array', self.toolbar, self._array_cb,
            tooltip=_('View images all at once.'), group=None)

        self.linear_button = radio_factory(
            'linear', self.toolbar, self._linear_cb,
            tooltip=_('View images one at a time.'), group=self.array_button)

        separator_factory(self.toolbar)

        self.save_as_image = button_factory(
            'image-saveoff', self.toolbar, self._do_save_as_image_cb,
            tooltip=_('Save as image'))

        separator_factory(self.toolbar)

        '''
        self._playback_button = button_factory(
            'media-playback-start-insensitive',  self.toolbar,
            self.playback_recording_cb, tooltip=_('Nothing to play'))
        '''

        separator_factory(toolbox.toolbar, True, False)

        stop_button = StopButton(self)
        stop_button.props.accelerator = '<Ctrl>q'
        toolbox.toolbar.insert(stop_button, -1)
        stop_button.show()

    def _array_cb(self, button=None):
        self.save_text_cb()
        self._game.set_mode('array')
        if self._uid is not None:
            self.check_audio_status()
            self.check_text_status()

    def _linear_cb(self, button=None):
        self.save_text_cb()
        self._game.set_mode('linear')
        if self._uid is not None:
            self.check_audio_status()
            self.check_text_status()

    def _new_game_cb(self, button=None):
        ''' Start a new game. '''
        if 'dirty' in self.metadata:
            alert = ConfirmationAlert()
            alert.props.title = _('Do you really want to load new images?')
            alert.props.msg = _('You have done work on this story.'
                                ' Do you want to overwrite it?')
            alert.connect('response', self._confirmation_alert_cb)
            self.add_alert(alert)
        else:
            self._game.new_game()

    def _confirmation_alert_cb(self, alert, response_id):
        self.remove_alert(alert)
        if response_id is Gtk.ResponseType.OK:
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
        self.save_text_cb()

    def _restore(self):
        ''' Restore the game state from metadata '''
        dot_list = []
        dots = self.metadata['dotlist'].split()
        for dot in dots:
            dot_list.append(int(dot))
        self._game.restore_game(dot_list)

    def _search_for_audio_note(self, obj_id):
        ''' Look to see if there is already a sound recorded for this
        dsobject: the object id is stored in a tag in the audio file. '''
        dsobjects, nobjects = datastore.find({'mime_type': ['audio/ogg']})
        # Look for tag that matches the target object id
        if self._game.get_mode() == 'array':
            target = obj_id
        else:
            target = '%s-%d' % (obj_id, self._game.current_image)

        for dsobject in dsobjects:
            if 'tags' in dsobject.metadata and \
               target in dsobject.metadata['tags']:
                _logger.debug('Found audio note')
                return dsobject
        return None

    def _do_save_as_image_cb(self, button=None):
        ''' Grab the current canvas and save it to the Journal. '''
        if self._uid is None:
            self._uid = generate_uid()

        if self._game.get_mode() == 'array':
            target = self._uid
        else:
            target = '%s-%d' % (self._uid, self._game.current_image)

        self._notify_successful_save(title=_('Save as image'))
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
        if self._alert is not None:
            self.remove_alert(self._alert)
            self._alert = None

    def record_cb(self, button=None):
        ''' Start/stop audio recording '''
        if self._grecord is None:
            _logger.debug('setting up grecord')
            self._grecord = Grecord(self)
        if self.recording:  # Was recording, so stop (and save?)
            _logger.debug('recording...True. Preparing to save.')
            self._game.set_record_icon_state(False)
            self._grecord.stop_recording_audio()
            self.recording = False
            self._notify_successful_save(title=_('Save recording'))
            GObject.timeout_add(100, self._wait_for_transcoding_to_finish)
        else:  # Wasn't recording, so start
            _logger.debug('recording...False. Start recording.')
            self._game.set_record_icon_state(True)
            self._grecord.record_audio()
            self.recording = True

    def _wait_for_transcoding_to_finish(self, button=None):
        while not self._grecord.transcoding_complete():
            time.sleep(1)
        if self._alert is not None:
            self.remove_alert(self._alert)
            self._alert = None
        self._save_recording()

    def playback_recording_cb(self, button=None):
        ''' Play back current recording '''
        if self.recording:  # Stop recording if we happen to be recording
            self.record_cb()

        path = os.path.join(self.datapath, 'output.ogg')
        if self._uid is not None:
            dsobject = self._search_for_audio_note(self._uid)
            if dsobject is not None:
                path = dsobject.file_path
        _logger.debug('Playback current recording from %s.' % (path))
        play_audio_from_file(path)
        return

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
            _logger.debug('setting file path to %s' %
                          (os.path.join(self.datapath, 'output.ogg')))
            dsobject.set_file_path(os.path.join(self.datapath, 'output.ogg'))
            datastore.write(dsobject)
            dsobject.destroy()

            # Enable playback after record is finished
            self._game.set_play_icon_state(True)

            # Always save an image with the recording.
            self._do_save_as_image_cb()
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
        if self._shared_activity is None:
            _logger.debug('Error: Failed to share or join activity ... \
                _shared_activity is null in _shared_cb()')
            return

        self.initiating = sharer
        self.waiting_for_hand = not sharer

        self.conn = self._shared_activity.telepathy_conn
        self.tubes_chan = self._shared_activity.telepathy_tubes_chan
        self.text_chan = self._shared_activity.telepathy_text_chan

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
        _logger.debug('Error: ListTubes() failed: %s' % (e))

    def _new_tube_cb(self, id, initiator, type, service, params, state):
        ''' Create a new tube. '''
        _logger.debug('New tube: ID=%d initator=%d type=%d service=%s'
                      ' params=%r state=%d' %
                      (id, initiator, type, service, params, state))

        if (type == telepathy.TUBE_TYPE_DBUS and service == SERVICE):
            if state == telepathy.TUBE_STATE_LOCAL_PENDING:
                self.tubes_chan[
                    telepathy.CHANNEL_TYPE_TUBES].AcceptDBusTube(id)

            tube_conn = TubeConnection(
                self.conn,
                self.tubes_chan[
                    telepathy.CHANNEL_TYPE_TUBES], id,
                group_iface=self.text_chan[telepathy.CHANNEL_INTERFACE_GROUP])

            self.chattube = ChatTube(tube_conn, self.initiating,
                                     self.event_received_cb)

    def _setup_dispatch_table(self):
        ''' Associate tokens with commands. '''
        self._processing_methods = {
            'n': [self._receive_new_images, 'get a new game grid'],
            'p': [self._receive_dot_click, 'get a dot click'],
            }

    def event_received_cb(self, event_message):
        ''' Data from a tube has arrived. '''
        if len(event_message) == 0:
            return
        try:
            command, payload = event_message.split('|', 2)
        except ValueError:
            _logger.debug('Could not split event message %s' % (event_message))
            return
        self._processing_methods[command][0](payload)

    def send_new_images(self):
        ''' Send a new image grid to all players '''
        self.send_event('n|%s' % (json_dump(self._game.save_game())))

    def _receive_new_images(self, payload):
        ''' Sharer can start a new game. '''
        dot_list = json_load(payload)
        self._game.restore_game(dot_list)

    def send_dot_click(self, dot, color):
        ''' Send a dot click to all the players '''
        self.send_event('p|%s' % (json_dump([dot, color])))

    def _receive_dot_click(self, payload):
        ''' When a dot is clicked, everyone should change its color. '''
        (dot, color) = json_load(payload)
        self._game.remote_button_press(dot, color)

    def send_event(self, entry):
        ''' Send event through the tube. '''
        if hasattr(self, 'chattube') and self.chattube is not None:
            self.chattube.SendText(entry)


class ChatTube(ExportedGObject):
    ''' Class for setting up tube for sharing '''

    def __init__(self, tube, is_initiator, stack_received_cb):
        super(ChatTube, self).__init__(tube, PATH)
        self.tube = tube
        self.is_initiator = is_initiator  # Are we sharing or joining activity?
        self.stack_received_cb = stack_received_cb
        self.stack = ''

        self.tube.add_signal_receiver(self.send_stack_cb, 'SendText', IFACE,
                                      path=PATH, sender_keyword='sender')

    def send_stack_cb(self, text, sender=None):
        if sender == self.tube.get_unique_name():
            return
        self.stack = text
        self.stack_received_cb(text)

    @signal(dbus_interface=IFACE, signature='s')
    def SendText(self, text):
        self.stack = text


def generate_uid():
    left = '%04x' % int(uniform(0, int(0xFFFF)))
    right = '%04x' % int(uniform(0, int(0xFFFF)))
    uid = '%s-%s' % (left, right)
    return uid.upper()
