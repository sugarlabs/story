# -*- coding: utf-8 -*-
#Copyright (c) 2012-14 Walter Bender
# Port to GTK3:
# Ignacio Rodriguez <ignaciorodriguez@sugarlabs.org>

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# You should have received a copy of the GNU General Public License
# along with this library; if not, write to the Free Software
# Foundation, 51 Franklin Street, Suite 500 Boston, MA 02110-1335 USA

from gi.repository import Gdk, GdkPixbuf, Gtk, GObject

import cairo
import os
import glob
import time
from random import uniform

from gettext import gettext as _

import logging
_logger = logging.getLogger('story-activity')

from sugar3.graphics import style

USE_ART4APPS = False
try:
    from art4apps import Art4Apps
    USE_ART4APPS = True
except ImportError:
    pass

from sprites import Sprites, Sprite
from utils import speak

PREV = 0
NEXT = 1
PREV_INACTIVE = 2
NEXT_INACTIVE = 3

RECORD_OFF = 0
RECORD_ON = 1
PLAY_OFF = 0
PLAY_ON = 1
SPEAK_OFF = 0
SPEAK_ON = 1

DOT_SIZE = 40
COLORS = ['#000000', '#a00000', '#907000', '#009000', '#0000ff', '#9000a0']


class Game():

    def __init__(self, canvas, parent=None, path=None, root=None, mode='array',
                 colors=['#A0FFA0', '#FF8080']):
        self._canvas = canvas
        self._parent = parent
        self._path = path
        self._root = root
        self._mode = mode
        self.current_image = 0
        self.playing = False
        self._timeout_id = None
        self._prev_mouse_pos = (0, 0)
        self._start_time = 0

        self._colors = ['#FFFFFF']
        self._colors.append(colors[0])
        self._colors.append(colors[1])

        self._canvas.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.BUTTON_MOTION_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK |
            Gdk.EventMask.POINTER_MOTION_HINT_MASK |
            Gdk.EventMask.TOUCH_MASK)

        self._canvas.connect('draw', self.__draw_cb)
        self._canvas.connect('event', self.__event_cb)

        self.configure(move=False)
        self.we_are_sharing = False

        self._start_time = 0
        self._timeout_id = None

        # Find the image files
        self._PATHS = glob.glob(os.path.join(self._path, 'images', '*.svg'))

        # Generate the sprites we'll need...
        self._sprites = Sprites(self._canvas)

        a = max(Gdk.Screen.width(), Gdk.Screen.height())
        b = min(Gdk.Screen.width(), Gdk.Screen.height())
        self._bg_pixbufs = []
        if self._parent.tablet_mode:  # text on top
            # landscape
            self._bg_pixbufs.append(svg_str_to_pixbuf(genhole(
                a, a, 2 * style.GRID_CELL_SIZE, style.DEFAULT_SPACING,
                a - 3 * style.GRID_CELL_SIZE + 2 * style.DEFAULT_SPACING,
                style.GRID_CELL_SIZE * 3 + style.DEFAULT_SPACING)))
            # portrait
            self._bg_pixbufs.append(svg_str_to_pixbuf(genhole(
                a, a, 2 * style.GRID_CELL_SIZE, style.DEFAULT_SPACING,
                b - 3 * style.GRID_CELL_SIZE + 2 * style.DEFAULT_SPACING,
                style.GRID_CELL_SIZE * 3 + style.DEFAULT_SPACING)))
        else:  # text on bottom
            # landscape
            self._bg_pixbufs.append(svg_str_to_pixbuf(genhole(
                a, a, 2 * style.GRID_CELL_SIZE,
                b - style.GRID_CELL_SIZE * 4 - style.DEFAULT_SPACING,
                a - 3 * style.GRID_CELL_SIZE + 2 * style.DEFAULT_SPACING,
                b - style.GRID_CELL_SIZE - style.DEFAULT_SPACING)))
            # portrait
            self._bg_pixbufs.append(svg_str_to_pixbuf(genhole(
                a, a, 2 * style.GRID_CELL_SIZE,
                a - style.GRID_CELL_SIZE * 4 - style.DEFAULT_SPACING,
                b - 3 * style.GRID_CELL_SIZE + 2 * style.DEFAULT_SPACING,
                a - style.GRID_CELL_SIZE - style.DEFAULT_SPACING)))

        if Gdk.Screen.width() > Gdk.Screen.height():
            self._bg = Sprite(self._sprites, 0, 0, self._bg_pixbufs[0])
        else:
            self._bg = Sprite(self._sprites, 0, 0, self._bg_pixbufs[1])
        self._bg.set_layer(-2)
        self._bg.type = 'background'

        size = 3 * self._dot_size + 4 * self._space
        x = int((Gdk.Screen.width() - size) / 2.)
        self._dots = []
        self._Dots = []  # larger dots for linear mode
        X = int((Gdk.Screen.width() - self._dot_size * 3) / 2.)
        Y = style.GRID_CELL_SIZE + self._yoff
        if self._parent.tablet_mode:
            yoffset = self._space * 2 + self._yoff
        else:
            yoffset = self._yoff
        for y in range(3):
            for x in range(3):
                xoffset = int((self._width - 3 * self._dot_size -
                               2 * self._space) / 2.)
                self._dots.append(
                    Sprite(self._sprites,
                           xoffset + x * (self._dot_size + self._space),
                           y * (self._dot_size + self._space) + yoffset,
                           self._new_dot_surface(color=self._colors[0])))
                self._dots[-1].type = -1  # No image
                self._dots[-1].set_label_attributes(72)
                self._dots[-1].set_label('?')

                self._Dots.append(
                    Sprite(self._sprites, X, Y, 
                           self._new_dot_surface(color=self._colors[0],
                                                 large=True)))
                self._Dots[-1].type = -1  # No image
                self._Dots[-1].set_label_attributes(72 * 3)
                self._Dots[-1].set_label('?')

        self.number_of_images = len(self._PATHS)
        if USE_ART4APPS:
            self._art4apps = Art4Apps()
            self.number_of_images = len(self._art4apps.get_words())

        self._record_pixbufs = []
        for icon in ['media-audio', 'media-audio-recording']:
            self._record_pixbufs.append(
                GdkPixbuf.Pixbuf.new_from_file_at_size(
                    os.path.join(self._root, 'icons', icon + '.svg'),
                    style.GRID_CELL_SIZE, style.GRID_CELL_SIZE))

        self._play_pixbufs = []
        for icon in ['play-inactive', 'play']:
            self._play_pixbufs.append(
                GdkPixbuf.Pixbuf.new_from_file_at_size(
                    os.path.join(self._root, 'icons', icon + '.svg'),
                    style.GRID_CELL_SIZE, style.GRID_CELL_SIZE))

        self._speak_pixbufs = []
        for icon in ['speak-inactive', 'speak']:
            self._speak_pixbufs.append(
                GdkPixbuf.Pixbuf.new_from_file_at_size(
                    os.path.join(self._root, 'icons', icon + '.svg'),
                    style.GRID_CELL_SIZE, style.GRID_CELL_SIZE))

        left = style.GRID_CELL_SIZE
        right = Gdk.Screen.width() - 2 * style.GRID_CELL_SIZE
        y0 = style.DEFAULT_SPACING
        y1 = y0 + style.GRID_CELL_SIZE
        y2 = y1 + style.GRID_CELL_SIZE
        if not self._parent.tablet_mode:
            dy = Gdk.Screen.height() - 4 * style.GRID_CELL_SIZE - \
                 2 * style.DEFAULT_SPACING
            y0 += dy
            y1 += dy
            y2 += dy
        y3 = int((Gdk.Screen.height() - 2 * style.GRID_CELL_SIZE) / 2)

        self._record = Sprite(self._sprites, right, y0,
                              self._record_pixbufs[RECORD_OFF])
        self._record.set_layer(1)
        self._record.type = 'record'

        self._play = Sprite(self._sprites, right, y1,
                            self._play_pixbufs[PLAY_OFF])
        self._play.set_layer(1)
        self._play.type = 'play-inactive'

        self._speak = Sprite(self._sprites, right, y2,
                            self._speak_pixbufs[SPEAK_OFF])
        self._speak.set_layer(1)
        self._speak.type = 'speak-inactive'

        self._next_prev_pixbufs = []
        for icon in ['go-previous', 'go-next', 'go-previous-inactive',
                     'go-next-inactive']:
            self._next_prev_pixbufs.append(
                GdkPixbuf.Pixbuf.new_from_file_at_size(
                    os.path.join(self._root, 'icons', icon + '.svg'),
                    style.GRID_CELL_SIZE, style.GRID_CELL_SIZE))

        self._prev = Sprite(self._sprites, left, y3,
                            self._next_prev_pixbufs[PREV_INACTIVE])
        self._prev.set_layer(1)
        self._prev.type = 'prev'
        if self._mode == 'array':
            self._prev.hide()

        self._next = Sprite(self._sprites, right, y3,
                            self._next_prev_pixbufs[NEXT])
        self._next.set_layer(1)
        self._next.type = 'next'
        if self._mode == 'array':
            self._next.hide()
        
    def configure(self, move=True):
        self._width = Gdk.Screen.width()
        self._height = Gdk.Screen.height() - style.GRID_CELL_SIZE
        if not move:
            if self._height < self._width:
                self._scale = self._height / (3 * DOT_SIZE * 1.2)
            else:
                self._scale = self._width / (3 * DOT_SIZE * 1.2)
            self._scale /= 1.5
            self._dot_size = int(DOT_SIZE * self._scale)
            if self._parent.tablet_mode:  # text on top
                self._yoff = style.GRID_CELL_SIZE * 3 + style.DEFAULT_SPACING
            else:
                self._yoff = style.DEFAULT_SPACING
            self._space = int(self._dot_size / 5.)
            return

        left = style.GRID_CELL_SIZE
        right = Gdk.Screen.width() - 2 * style.GRID_CELL_SIZE
        y0 = style.DEFAULT_SPACING
        y1 = y0 + style.GRID_CELL_SIZE
        y2 = y1 + style.GRID_CELL_SIZE
        if not self._parent.tablet_mode:
            dy = Gdk.Screen.height() - 4 * style.GRID_CELL_SIZE - \
                 2 * style.DEFAULT_SPACING
            y0 += dy
            y1 += dy
            y2 += dy
        y3 = int((Gdk.Screen.height() - 2 * style.GRID_CELL_SIZE) / 2)
        self._record.move((right, y0))
        self._play.move((right, y1))
        self._speak.move((right, y2))
        self._prev.move((left, y3))
        self._next.move((right, y3))

        # Move the dots
        X = int((Gdk.Screen.width() - self._dot_size * 3) / 2.)
        Y = style.GRID_CELL_SIZE + self._yoff
        if self._parent.tablet_mode:
            yoffset = self._space * 2 + self._yoff
        else:
            yoffset = self._yoff
        for y in range(3):
            for x in range(3):
                xoffset = int((self._width - 3 * self._dot_size -
                               2 * self._space) / 2.)
                self._dots[x + y * 3].move(
                           (xoffset + x * (self._dot_size + self._space),
                           y * (self._dot_size + self._space) + yoffset))
                self._Dots[x + y * 3].move((X, Y))

        # switch orientation the bg sprite
        if Gdk.Screen.width() > Gdk.Screen.height():
            self._bg.set_image(self._bg_pixbufs[0])
        else:
            self._bg.set_image(self._bg_pixbufs[1])
        self._bg.set_layer(-2)

    def set_speak_icon_state(self, state):
        if state:
            self._speak.set_image(self._speak_pixbufs[SPEAK_ON])
            self._speak.type = 'speak'
        else:
            self._speak.set_image(self._speak_pixbufs[SPEAK_OFF])
            self._speak.type = 'speak-inactive'
        self._speak.set_layer(1)

    def set_record_icon_state(self, state):
        if state:
            self._record.set_image(self._record_pixbufs[RECORD_ON])
        else:
            self._record.set_image(self._record_pixbufs[RECORD_OFF])
        self._record.set_layer(1)

    def set_play_icon_state(self, state):
        if state:
            self._play.set_image(self._play_pixbufs[PLAY_ON])
            self._play.type = 'play'
        else:
            self._play.set_image(self._play_pixbufs[PLAY_OFF])
            self._play.type = 'play-inactive'
        self._play.set_layer(1)

    def autoplay(self):
        self.set_mode('linear')  # forces current image to 0
        self.playing = True
        self._autonext(next=False)

    def stop(self):
        self.playing = False
        if self._parent.audio_process is not None:
            self._parent.audio_process.terminate()
            self._parent.audio_process = None
        if self._timeout_id is not None:
            GObject.source_remove(self._timeout_id)
            self._timeout_id = None
        self._parent.autoplay_button.set_icon_name('media-playback-start')
        self._parent.autoplay_button.set_tooltip(_('Play'))

    def _autonext(self, next=True):
        self._timeout_id = None
        if not self.playing:
            return

        if next:
            self._Dots[self.current_image].hide()
            self.current_image += 1
            self._Dots[self.current_image].set_layer(100)
            if self.current_image == 8:
                self._next.set_image(
                    self._next_prev_pixbufs[NEXT_INACTIVE])
                self._next.set_layer(1)
            self._prev.set_image(self._next_prev_pixbufs[PREV])
            self._prev.set_layer(1)
        self._parent.check_audio_status()
        self._parent.check_text_status()
        logging.debug('autoplay %d' % self.current_image)
        GObject.idle_add(self._play_sound)

    def _poll_audio(self):
        if self._parent.audio_process is None:  # Already stopped?
            return

        if self._parent.audio_process.poll() is None:
            GObject.timeout_add(200, self._poll_audio)
        else:
            self._parent.audio_process = None
            self._next_image()

    def _play_sound(self):
        self._start_time = time.time()

        # Either play back a recording or speak the text
        if self._play.type == 'play':
            self._parent.playback_recording_cb()
            self._poll_audio()
        elif self._speak.type == 'speak':
            bounds = self._parent.text_buffer.get_bounds()
            text = self._parent.text_buffer.get_text(
                bounds[0], bounds[1], True)
            speak(text)
            self._next_image()

    def _next_image(self):
        accumulated_time = int(time.time() - self._start_time)
        if accumulated_time < 5:
            pause = 5 - accumulated_time
        else:
            pause = 1
        if self.playing and self.current_image < 8:
            self._timeout_id = GObject.timeout_add(pause * 1000,
                                                   self._autonext)
        else:
            self.stop()

    def __event_cb(self, win, event):
        ''' The mouse button was pressed. Is it on a sprite? or
            there was a gesture. '''

        left = right = False

        if event.type in (Gdk.EventType.TOUCH_BEGIN,
                          Gdk.EventType.TOUCH_CANCEL,
                          Gdk.EventType.TOUCH_END,
                          Gdk.EventType.BUTTON_PRESS,
                          Gdk.EventType.BUTTON_RELEASE):

            if self.playing:
                self.stop()

            if self._parent.audio_process is not None:
                self._parent.audio_process.terminate()
                self._parent.audio_process = None

            x = int(event.get_coords()[1])
            y = int(event.get_coords()[2])

            # logging.error('event x %d y %d type %s', x, y, event.type)
            if event.type in (Gdk.EventType.TOUCH_BEGIN,
                              Gdk.EventType.BUTTON_PRESS):
                self._prev_mouse_pos = (x, y)
            elif event.type in (Gdk.EventType.TOUCH_END,
                                Gdk.EventType.BUTTON_RELEASE):

                new_mouse_pos = (x, y)
                mouse_movement = (new_mouse_pos[0] - self._prev_mouse_pos[0],
                                  new_mouse_pos[1] - self._prev_mouse_pos[1])

                # horizontal gestures only
                if (abs(mouse_movement[0]) / 5) > abs(mouse_movement[1]):
                    if abs(mouse_movement[0]) > abs(mouse_movement[1]):
                        if mouse_movement[0] > 0:
                            right = True
                        else:
                            left = True

        if event.type in (Gdk.EventType.TOUCH_END,
                          Gdk.EventType.BUTTON_RELEASE):
            spr = self._sprites.find_sprite((x, y))
            if left or right or spr is not None:
                if spr.type in ['record', 'play', 'play-inactive', 'speak',
                                'speak-inactive']:
                    if spr.type == 'record':
                        self._parent.record_cb()
                    elif spr.type == 'play':
                        self._parent.playback_recording_cb()
                    elif spr.type == 'speak':
                        bounds = self._parent.text_buffer.get_bounds()
                        text = self._parent.text_buffer.get_text(
                            bounds[0], bounds[1], True)
                        speak(text)
                    return
                elif self._mode == 'array':
                    return

                self._parent.speak_text_cb()

                if self._parent.recording:
                    self._parent.record_cb()

                if (left or spr.type == 'prev') and self.current_image > 0:
                    self._Dots[self.current_image].hide()
                    self.current_image -= 1
                    self._Dots[self.current_image].set_layer(100)
                    if self.current_image == 0:
                        self._prev.set_image(
                            self._next_prev_pixbufs[PREV_INACTIVE])
                    self._next.set_image(self._next_prev_pixbufs[NEXT])
                elif (right or spr.type == 'next') and self.current_image < 8:
                    self._Dots[self.current_image].hide()
                    self.current_image += 1
                    self._Dots[self.current_image].set_layer(100)
                    if self.current_image == 8:
                        self._next.set_image(
                            self._next_prev_pixbufs[NEXT_INACTIVE])
                    self._prev.set_image(self._next_prev_pixbufs[PREV])
                elif spr.type not in ['prev', 'background'] and \
                     self.current_image < 8:
                    self._Dots[self.current_image].hide()
                    self.current_image += 1
                    self._Dots[self.current_image].set_layer(100)
                    if self.current_image == 8:
                        self._next.set_image(
                            self._next_prev_pixbufs[NEXT_INACTIVE])
                    self._prev.set_image(self._next_prev_pixbufs[PREV])
                self._parent.check_audio_status()
                self._parent.check_text_status()
                self._prev.set_layer(1)
                self._next.set_layer(1)
        return False

    def get_mode(self):
        return self._mode

    def set_mode(self, mode):
        self.current_image = 0
        self._prev.set_image(self._next_prev_pixbufs[PREV_INACTIVE])
        self._next.set_image(self._next_prev_pixbufs[NEXT])
        if mode == 'array':
            self._mode = 'array'
            self._prev.hide()
            self._next.hide()
        else:
            self._mode = 'linear'
            self._prev.set_layer(1)
            self._next.set_layer(1)

        for i in range(9):
            if self._mode == 'array':
                self._dots[i].set_layer(100)
                self._Dots[i].hide()
            else:
                self._dots[i].hide()
                if self.current_image == i:
                    self._Dots[i].set_layer(100)
                else:
                    self._Dots[i].hide()

    def _all_clear(self):
        ''' Things to reinitialize when starting up a new game. '''
        if self._timeout_id is not None:
            GObject.source_remove(self._timeout_id)

        self.set_mode(self._mode)

        if self._mode == 'array':
            for dot in self._dots:
                if dot.type != -1:
                    dot.type = -1
                    dot.set_shape(self._new_dot_surface(
                        self._colors[abs(dot.type)]))
                    dot.set_label('?')
        else:
            for dot in self._Dots:
                if dot.type != -1:
                    dot.type = -1
                    dot.set_shape(self._new_dot_surface(
                        self._colors[abs(dot.type)],
                        large=True))
                    dot.set_label('?')
        self._dance_counter = 0
        self._dance_step()

    def _dance_step(self):
        ''' Short animation before loading new game '''
        if self._mode == 'array':
            for dot in self._dots:
                dot.set_shape(self._new_dot_surface(
                    self._colors[int(uniform(0, 3))]))
        else:
            self._Dots[0].set_shape(self._new_dot_surface(
                self._colors[int(uniform(0, 3))],
                large=True))

        self._dance_counter += 1
        if self._dance_counter < 10:
            self._timeout_id = GObject.timeout_add(500, self._dance_step)
        else:
            self._new_images()

    def new_game(self):
        ''' Start a new game. '''
        self._all_clear()

    def _new_images(self):
        ''' Select pictures at random '''
        for i in range(9):
            self._dots[i].set_label('')
            self._dots[i].type = int(uniform(0, self.number_of_images))
            self._dots[i].set_shape(self._new_dot_surface(
                image=self._dots[i].type))

            self._Dots[i].set_label('')
            self._Dots[i].type = self._dots[i].type
            self._Dots[i].set_shape(self._new_dot_surface(
                image=self._Dots[i].type, large=True))

            if self._mode == 'array':
                self._dots[i].set_layer(100)
                self._Dots[i].hide()
            else:
                if self.current_image == i:
                    self._Dots[i].set_layer(100)
                else:
                    self._Dots[i].hide()
                self._dots[i].hide()

        if self.we_are_sharing:
            _logger.debug('sending a new game')
            self._parent.send_new_images()

    def restore_game(self, dot_list):
        ''' Restore a game from the Journal or share '''

        self.set_mode(self._mode)

        for i, dot in enumerate(dot_list):
            self._dots[i].type = dot
            self._dots[i].set_shape(self._new_dot_surface(
                image=self._dots[i].type))
            self._dots[i].set_label('')

            self._Dots[i].type = dot
            self._Dots[i].set_shape(self._new_dot_surface(
                image=self._Dots[i].type, large=True))
            self._Dots[i].set_label('')

            if self._mode == 'array':
                self._dots[i].set_layer(100)
                self._Dots[i].hide()
            else:
                if self.current_image == i:
                    self._Dots[i].set_layer(100)
                else:
                    self._Dots[i].hide()
                self._dots[i].hide()

    def save_game(self):
        ''' Return dot list for saving to Journal or
        sharing '''
        dot_list = []
        for dot in self._dots:
            dot_list.append(dot.type)
        return dot_list

    def set_sharing(self, share=True):
        _logger.debug('enabling sharing')
        self.we_are_sharing = share

    def _grid_to_dot(self, pos):
        ''' calculate the dot index from a column and row in the grid '''
        return pos[0] + pos[1] * 3

    def _dot_to_grid(self, dot):
        ''' calculate the grid column and row for a dot '''
        return [dot % 3, int(dot / 3)]

    def __draw_cb(self, canvas, cr):
        self._sprites.redraw_sprites(cr=cr)

    def __expose_cb(self, win, event):
        ''' Callback to handle window expose events '''
        self.do_expose_event(event)
        return True

    # Handle the expose-event by drawing
    def do_expose_event(self, event):
        # Create the cairo context
        cr = self._canvas.window.cairo_create()

        # Restrict Cairo to the exposed area; avoid extra work
        cr.rectangle(event.area.x, event.area.y,
                     event.area.width, event.area.height)
        cr.clip()

        # Refresh sprite list
        if cr is not None:
            self._sprites.redraw_sprites(cr=cr)

    def _destroy_cb(self, win, event):
        Gtk.main_quit()

    def export(self):
        ''' Write dot to cairo surface. '''
        if self._mode == 'array':
            w = h = int(4 * self._space + 3 * self._dot_size)
            png_surface = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
            cr = cairo.Context(png_surface)
            cr.set_source_rgb(192, 192, 192)
            cr.rectangle(0, 0, w, h)
            cr.fill()
            for i in range(9):
                y = self._space + int(i / 3.) * (self._dot_size + self._space)
                x = self._space + (i % 3) * (self._dot_size + self._space)
                cr.save()
                cr.set_source_surface(self._dots[i].images[0], x, y)
                cr.rectangle(x, y, self._dot_size, self._dot_size)
                cr.fill()
                cr.restore()
        else:
            w = h = int(2 * self._space + 3 * self._dot_size)
            png_surface = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
            cr = cairo.Context(png_surface)
            cr.set_source_rgb(192, 192, 192)
            cr.rectangle(0, 0, w, h)
            cr.fill()
            y = self._space
            x = self._space
            cr.save()
            cr.set_source_surface(self._Dots[self.current_image].images[0],
                                  x, y)
            cr.rectangle(x, y, 3 * self._dot_size, 3 * self._dot_size)
            cr.fill()
            cr.restore()

        return png_surface

    def _new_dot_surface(self, color='#000000', image=None, large=False):
        ''' generate a dot of a color color '''

        if large:
            size = self._dot_size * 3
        else:
            size = self._dot_size
        self._svg_width = size
        self._svg_height = size

        if image is None:  # color dot
            self._stroke = color
            self._fill = color
            pixbuf = svg_str_to_pixbuf(
                self._header() +
                self._circle(size / 2., size / 2., size / 2.) +
                self._footer())
        else:
            if USE_ART4APPS:
                word = self._art4apps.get_words()[image]
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(
                        self._art4apps.get_image_filename(word), size, size)
                except Exception, e:
                    _logger.error('new dot surface %s %s: %s' %
                                  (image, word, e))
                    word = 'zebra'  # default in case image is not found
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(
                        self._art4apps.get_image_filename(word), size, size)
            else:
                # Set SVG color
                color = COLORS[int(uniform(0, 6))]
                fd = open(os.path.join(self._path, self._PATHS[image]), 'r')
                svg_string = ''
                for line in fd:
                    svg_string += line.replace('#000000', color)
                fd.close()
                pixbuf = svg_str_to_pixbuf(svg_string, w=size, h=size)

        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                     self._svg_width, self._svg_height)
        context = cairo.Context(surface)
        Gdk.cairo_set_source_pixbuf(context, pixbuf, 0, 0)
        context.rectangle(0, 0, self._svg_width, self._svg_height)
        context.fill()
        return surface

    def _header(self):
        return '<svg\n' + 'xmlns:svg="http://www.w3.org/2000/svg"\n' + \
            'xmlns="http://www.w3.org/2000/svg"\n' + \
            'xmlns:xlink="http://www.w3.org/1999/xlink"\n' + \
            'version="1.1"\n' + 'width="' + str(self._svg_width) + '"\n' + \
            'height="' + str(self._svg_height) + '">\n'

    def _rect(self, w, h, x, y):
        svg_string = '       <rect\n'
        svg_string += '          width="%f"\n' % (w)
        svg_string += '          height="%f"\n' % (h)
        svg_string += '          rx="%f"\n' % (0)
        svg_string += '          ry="%f"\n' % (0)
        svg_string += '          x="%f"\n' % (x)
        svg_string += '          y="%f"\n' % (y)
        svg_string += 'style="fill:#000000;stroke:#000000;"/>\n'
        return svg_string

    def _circle(self, r, cx, cy):
        return '<circle style="fill:' + str(self._fill) + ';stroke:' + \
            str(self._stroke) + ';" r="' + str(r - 0.5) + '" cx="' + \
            str(cx) + '" cy="' + str(cy) + '" />\n'

    def _footer(self):
        return '</svg>\n'


def genblank(w, h, colors, stroke_width=1.0):
    svg = SVG()
    svg.set_colors(colors)
    svg.set_stroke_width(stroke_width)
    svg_string = svg.header(w, h)
    svg_string += svg.footer()
    return svg_string


def genhole(w, h, x1, y1, x2, y2):
    return \
'<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n' + \
'<svg ' + \
'   width="%d"' % w + \
'   height="%d">\n' % h + \
'    <path ' + \
'       d="m 0,0 0,%d %d,0 0,%d z m %d,%d %d,0 0,%d %d,0 z"' \
% (h, w, -h, x1, y1, x2 - x1, y2 - y1, x1 - x2) + \
'       style="fill:#FFFFFF;fill-opacity:1;stroke:none;stroke-width:3.5;" />\n' + \
'</svg>'


class SVG:
    ''' SVG generators '''

    def __init__(self):
        self._scale = 1
        self._stroke_width = 1
        self._fill = '#FFFFFF'
        self._stroke = '#FFFFFF'

    def _svg_style(self, extras=""):
        return "%s%s%s%s%s%f%s%s%s" % ("style=\"fill:", self._fill, ";stroke:",
                                       self._stroke, ";stroke-width:",
                                       self._stroke_width, ";", extras,
                                       "\" />\n")

    def _svg_rect(self, w, h, rx, ry, x, y):
        svg_string = "       <rect\n"
        svg_string += "          width=\"%f\"\n" % (w)
        svg_string += "          height=\"%f\"\n" % (h)
        svg_string += "          rx=\"%f\"\n" % (rx)
        svg_string += "          ry=\"%f\"\n" % (ry)
        svg_string += "          x=\"%f\"\n" % (x)
        svg_string += "          y=\"%f\"\n" % (y)
        self.set_stroke_width(self._stroke_width)
        svg_string += self._svg_style()
        return svg_string

    def _background(self, w=80, h=60, scale=1):
        return self._svg_rect((w - 0.5) * scale, (h - 0.5) * scale,
                              1, 1, 0.25, 0.25)

    def header(self, w=80, h=60, scale=1, background=True):
        svg_string = "<?xml version=\"1.0\" encoding=\"UTF-8\""
        svg_string += " standalone=\"no\"?>\n"
        svg_string += "<!-- Created with Emacs -->\n"
        svg_string += "<svg\n"
        svg_string += "   xmlns:svg=\"http://www.w3.org/2000/svg\"\n"
        svg_string += "   xmlns=\"http://www.w3.org/2000/svg\"\n"
        svg_string += "   version=\"1.0\"\n"
        svg_string += "%s%f%s" % ("   width=\"", scale * w * self._scale,
                                  "\"\n")
        svg_string += "%s%f%s" % ("   height=\"", scale * h * self._scale,
                                  "\">\n")
        svg_string += "%s%f%s%f%s" % ("<g\n       transform=\"matrix(",
                                      self._scale, ",0,0,", self._scale,
                                      ",0,0)\">\n")
        if background:
            svg_string += self._background(w, h, scale)
        return svg_string

    def footer(self):
        svg_string = "</g>\n"
        svg_string += "</svg>\n"
        return svg_string

    def set_scale(self, scale=1.0):
        self._scale = scale

    def set_colors(self, colors):
        self._stroke = colors[0]
        self._fill = colors[1]

    def set_stroke_width(self, stroke_width=1.0):
        self._stroke_width = stroke_width


def svg_str_to_pixbuf(svg_string, w=None, h=None):
    ''' Load pixbuf from SVG string '''
    # Admito que fue la parte mas dificil..
    pl = GdkPixbuf.PixbufLoader.new_with_type('svg')
    if w is not None:
        pl.set_size(w, h)
    pl.write(svg_string)
    pl.close()
    return pl.get_pixbuf()
