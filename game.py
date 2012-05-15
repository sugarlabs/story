# -*- coding: utf-8 -*-
#Copyright (c) 2012 Walter Bender

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# You should have received a copy of the GNU General Public License
# along with this library; if not, write to the Free Software
# Foundation, 51 Franklin Street, Suite 500 Boston, MA 02110-1335 USA


import gtk
import gobject
import cairo
import os
import glob
from random import uniform

from gettext import gettext as _

import logging
_logger = logging.getLogger('search-activity')

try:
    from sugar.graphics import style
    GRID_CELL_SIZE = style.GRID_CELL_SIZE
except ImportError:
    GRID_CELL_SIZE = 0

from sprites import Sprites, Sprite


DOT_SIZE = 40
COLORS = ['#000000', '#ff0000', '#907000', '#009000', '#0000ff', '#9000a0']


class Game():

    def __init__(self, canvas, parent=None, path=None,
                 colors=['#A0FFA0', '#FF8080']):
        self._canvas = canvas
        self._parent = parent
        self._parent.show_all()
        self._path = path

        self._colors = ['#FFFFFF']
        self._colors.append(colors[0])
        self._colors.append(colors[1])

        self._canvas.set_flags(gtk.CAN_FOCUS)
        self._canvas.connect("expose-event", self._expose_cb)

        self._width = gtk.gdk.screen_width()
        self._height = gtk.gdk.screen_height() - (GRID_CELL_SIZE * 1.5)
        self._scale = self._height / (3 * DOT_SIZE * 1.2)
        self._scale /= 1.5
        self._dot_size = int(DOT_SIZE * self._scale)
        self._space = int(self._dot_size / 5.)
        self.we_are_sharing = False

        self._start_time = 0
        self._timeout_id = None

        # Find the image files
        self._PATHS = glob.glob(os.path.join(self._path, 'images', '*.svg'))

        # Generate the sprites we'll need...
        self._sprites = Sprites(self._canvas)
        self._dots = []
        yoffset = self._space * 2  # int(self._space / 2.)
        for y in range(3):
            for x in range(3):
                xoffset = int((self._width - 3 * self._dot_size - \
                                   2 * self._space) / 2.)
                self._dots.append(
                    Sprite(self._sprites,
                           xoffset + x * (self._dot_size + self._space),
                           y * (self._dot_size + self._space) + yoffset,
                           self._new_dot_surface(color=self._colors[0])))
                self._dots[-1].type = -1  # No image
                self._dots[-1].set_label_attributes(72)

    def _all_clear(self):
        ''' Things to reinitialize when starting up a new game. '''
        if self._timeout_id is not None:
            gobject.source_remove(self._timeout_id)

        for dot in self._dots:
            if dot.type != -1:
                dot.type = -1
                dot.set_shape(self._new_dot_surface(
                        self._colors[abs(dot.type)]))
            dot.set_label('?')
        self._dance_counter = 0
        self._dance_step()

    def _dance_step(self):
        ''' Short animation before loading new game '''
        for dot in self._dots:
            dot.set_shape(self._new_dot_surface(
                    self._colors[int(uniform(0, 3))]))
        self._dance_counter += 1
        if self._dance_counter < 10:
            self._timeout_id = gobject.timeout_add(500, self._dance_step)
        else:
            self._new_game()

    def new_game(self):
        ''' Start a new game. '''
        self._all_clear()

    def _new_game(self):
        ''' Select pictures at random '''
        for i in range(3 * 3):
            self._dots[i].set_label('')
            self._dots[i].type = int(uniform(0, len(self._PATHS)))
            _logger.debug(self._dots[i].type)
            self._dots[i].set_shape(self._new_dot_surface(
                    image=self._dots[i].type))

        if self.we_are_sharing:
            _logger.debug('sending a new game')
            self._parent.send_new_game()

    def restore_game(self, dot_list):
        ''' Restore a game from the Journal or share '''
        for i, dot in enumerate(dot_list):
            self._dots[i].type = dot
            self._dots[i].set_shape(self._new_dot_surface(
                    image=self._dots[i].type))

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

    def _expose_cb(self, win, event):
        self.do_expose_event(event)

    def do_expose_event(self, event):
        ''' Handle the expose-event by drawing '''
        # Restrict Cairo to the exposed area
        cr = self._canvas.window.cairo_create()
        cr.rectangle(event.area.x, event.area.y,
                event.area.width, event.area.height)
        cr.clip()
        # Refresh sprite list
        self._sprites.redraw_sprites(cr=cr)

    def _destroy_cb(self, win, event):
        gtk.main_quit()

    def export(self):
        ''' Write dot to cairo surface. '''
        w = h = 4 * self._space + 3 * self._dot_size
        png_surface = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
        cr = cairo.Context(png_surface)
        cr.set_source_rgb(192, 192, 192)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        for i in range(9):
            y = self._space + int(i / 3.) * (self._dot_size + self._space)
            x = self._space + (i % 3) * (self._dot_size + self._space)
            cr.save()
            cr = gtk.gdk.CairoContext(cr)
            cr.set_source_surface(self._dots[i].cached_surfaces[0], x, y)
            cr.rectangle(x, y, self._dot_size, self._dot_size)
            cr.fill()
            cr.restore()
        return png_surface

    def _new_dot_surface(self, color='#000000', image=None):
        ''' generate a dot of a color color '''
        self._dot_cache = {}
        if image is not None:
            color = COLORS[int(uniform(0, 6))]
            fd = open(os.path.join(self._path, self._PATHS[image]), 'r')
            svg_string = ''
            for line in fd:
                svg_string += line.replace('#000000', color)
            fd.close()
            pixbuf = svg_str_to_pixbuf(svg_string, w=self._dot_size,
                                       h = self._dot_size)
            '''
            pixbuf = gtk.gdk.pixbuf_new_from_file_at_size(
                os.path.join(self._path, self._PATHS[image]),
                self._dot_size, self._dot_size)
            '''
        else:
            if color in self._dot_cache:
                return self._dot_cache[color]
            self._stroke = color
            self._fill = color
            self._svg_width = self._dot_size
            self._svg_height = self._dot_size

            i = self._colors.index(color)
            pixbuf = svg_str_to_pixbuf(
                self._header() + \
                    self._circle(self._dot_size / 2., self._dot_size / 2.,
                                 self._dot_size / 2.) + \
                    self._footer())
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                     self._svg_width, self._svg_height)
        context = cairo.Context(surface)
        context = gtk.gdk.CairoContext(context)
        context.set_source_pixbuf(pixbuf, 0, 0)
        context.rectangle(0, 0, self._svg_width, self._svg_height)
        context.fill()
        if image is None:
            self._dot_cache[color] = surface
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


def svg_str_to_pixbuf(svg_string, w=None, h=None):
    ''' Load pixbuf from SVG string '''
    pl = gtk.gdk.PixbufLoader('svg') 
    if w is not None:
        pl.set_size(w, h)
    pl.write(svg_string)
    pl.close()
    return pl.get_pixbuf()
