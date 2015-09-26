#       ui.py
#
#       Copyright 2010 Thomas Jost <thomas.jost@gmail.com>
#
#       This program is free software; you can redistribute it and/or modify
#       it under the terms of the GNU General Public License as published by
#       the Free Software Foundation; either version 2 of the License, or
#       (at your option) any later version.
#
#       This program is distributed in the hope that it will be useful,
#       but WITHOUT ANY WARRANTY; without even the implied warranty of
#       MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#       GNU General Public License for more details.
#
#       You should have received a copy of the GNU General Public License
#       along with this program; if not, write to the Free Software
#       Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#       MA 02110-1301, USA.

"""
:mod:`pympress.ui` -- GUI management
------------------------------------

This module contains the whole graphical user interface of pympress, which is
made of two separate windows: the Content window, which displays only the
current page in full size, and the Presenter window, which displays both the
current and the next page, as well as a time counter and a clock.

Both windows are managed by the :class:`~pympress.ui.UI` class.
"""

from __future__ import print_function

import os
import sys
import time

import pkg_resources

import pygtk
pygtk.require('2.0')
import gobject
import gtk
import pango

import pympress.pixbufcache
import pympress.util

#: "Regular" PDF file (without notes)
PDF_REGULAR      = 0
#: Content page (left side) of a PDF file with notes
PDF_CONTENT_PAGE = 1
#: Notes page (right side) of a PDF file with notes
PDF_NOTES_PAGE   = 2

class SlideSelector(gtk.SpinButton):
    ui = None
    maxpage = -1
    timer = -1
    def __init__(self, parent, maxpage):
        gtk.SpinButton.__init__(self)

        self.ui = parent
        self.maxpage = maxpage

        self.set_adjustment(gtk.Adjustment(lower=1, upper=maxpage, step_incr=1))
        self.set_update_policy(gtk.UPDATE_ALWAYS)
        self.set_numeric(True)

        self.connect('changed', self.on_changed)
        self.connect("key-press-event", self.on_keypress)
        self.connect("key-release-event", self.on_keyup)
        self.connect("editing-done", self.done)
        self.connect("insert-text", self.on_changed)

    def on_keyup(self, widget, event):
        if event.type == gtk.gdk.KEY_PRESS and gtk.gdk.keyval_name(event.keyval).upper() == "G":
            return False
        return gtk.SpinButton.do_key_release_event(self, event)

    def done(self, *args):
        self.ui.restore_current_label()
        self.ui.doc.goto(self.get_page())

    def cancel(self, *args):
        self.ui.restore_current_label()
        self.ui.on_page_change()

    def on_changed(self, *args):
        self.ui.page_preview(self.get_page())

    def force_update(self, *args):
        self.timer = -1
        self.set_value(float(self.get_buffer().get_text()))
        self.on_changed()
        return False

    def get_page(self):
        return max(1, min(self.maxpage, self.get_value_as_int()))-1

    def on_keypress(self, widget, event):
        if event.type == gtk.gdk.KEY_PRESS:
            name = gtk.gdk.keyval_name(event.keyval)

            # Return key --> restore label and goto page
            if name == "Return" or name == "KP_Return":
                self.done()
            # Escape key --> just restore the label
            elif name == "Escape":
                self.cancel()
            elif name == 'Home':
                self.set_value(1)
            elif name == 'End':
                self.set_value(self.maxpage)

            elif name in map(str, range(10)):
                if self.timer >= 0:
                    gobject.source_remove(self.timer)
                self.timer = gobject.timeout_add(250, self.force_update)
                return gtk.SpinButton.do_key_press_event(self, event)

            elif name.upper() in ['A', 'UP', 'DOWN', 'LEFT', 'RIGHT', 'BACKSPACE']:
                return gtk.SpinButton.do_key_press_event(self, event)
            else:
                return False

            if self.timer >= 0:
                gobject.source_remove(self.timer)

            return True

        elif event.type == gtk.gdk.SCROLL:
            if event.direction in [gtk.gdk.SCROLL_RIGHT, gtk.gdk.SCROLL_DOWN]:
                self.set_value(self.get_value_as_int() + 1)
            else:
                self.set_value(self.get_value_as_int() - 1)

        return False

class UI:
    """Pympress GUI management."""

    #: :class:`~pympress.pixbufcache.PixbufCache` instance.
    cache = None

    #: Content window, as a :class:`gtk.Window` instance.
    c_win = gtk.Window(gtk.WINDOW_TOPLEVEL)
    #: :class:`~gtk.AspectFrame` for the Content window.
    c_frame = gtk.AspectFrame(ratio=4./3., obey_child=False)
    #: :class:`~gtk.DrawingArea` for the Content window.
    c_da = gtk.DrawingArea()

    #: Presentation window, as a :class:`gtk.Window` instance.
    p_win = gtk.Window(gtk.WINDOW_TOPLEVEL)
    #: :class:`~gtk.AspectFrame` for the current slide in the Presenter window.
    p_frame_cur = gtk.AspectFrame(yalign=1, ratio=4./3., obey_child=False)
    #: :class:`~gtk.DrawingArea` for the current slide in the Presenter window.
    p_da_cur = gtk.DrawingArea()
    #: Slide counter :class:`~gtk.Label` for the current slide.
    label_cur = gtk.Label()
    #: Slide counter :class:`~gtk.Label` for the last slide.
    label_last = gtk.Label()
    #: :class:`~gtk.EventBox` associated with the slide counter label in the Presenter window.
    eb_cur = gtk.EventBox()
    #: forward keystrokes to the Content window even if the window manager puts Presenter on top
    editing_cur = False
    #: :class:`~gtk.SpinButton` used to switch to another slide by typing its number.
    spin_cur = None

    #: :class:`~gtk.AspectFrame` for the next slide in the Presenter window.
    p_frame_next = gtk.AspectFrame(yalign=1, ratio=4./3., obey_child=False)
    #: :class:`~gtk.DrawingArea` for the next slide in the Presenter window.
    p_da_next = gtk.DrawingArea()

    #: Elapsed time :class:`~gtk.Label`.
    label_time = gtk.Label()
    #: Clock :class:`~gtk.Label`.
    label_clock = gtk.Label()

    #: Time at which the counter was started.
    start_time = 0
    #: Time elapsed since the beginning of the presentation.
    delta = 0
    #: Timer paused status.
    paused = True

    #: Fullscreen toggle. By default, don't start in fullscreen mode.
    fullscreen = False

    #: Current :class:`~pympress.document.Document` instance.
    doc = None

    #: Whether to use notes mode or not
    notes_mode = False

    #: number of page currently displayed in Controller window's miniatures
    page_preview_nb = 0

    def __init__(self, doc):
        """
        :param doc: the current document
        :type  doc: :class:`pympress.document.Document`
        """
        black = gtk.gdk.Color(0, 0, 0)

        # Common to both windows
        icon_list = pympress.util.load_icons()

        # Pixbuf cache
        self.cache = pympress.pixbufcache.PixbufCache(doc)

        # Use notes mode by default if the document has notes
        self.notes_mode = doc.has_notes()

        # Content window
        self.c_win.set_title("pympress content")
        self.c_win.set_default_size(800, 600)
        self.c_win.modify_bg(gtk.STATE_NORMAL, black)
        self.c_win.connect("delete-event", gtk.main_quit)
        self.c_win.set_icon_list(*icon_list)

        self.c_frame.modify_bg(gtk.STATE_NORMAL, black)

        self.c_da.modify_bg(gtk.STATE_NORMAL, black)
        self.c_da.connect("expose-event", self.on_expose)
        self.c_da.set_name("c_da")
        if self.notes_mode:
            self.cache.add_widget("c_da", pympress.document.PDF_CONTENT_PAGE)
        else:
            self.cache.add_widget("c_da", pympress.document.PDF_REGULAR)
        self.c_da.connect("configure-event", self.on_configure)

        self.c_frame.add(self.c_da)
        self.c_win.add(self.c_frame)

        self.c_win.add_events(gtk.gdk.KEY_PRESS_MASK | gtk.gdk.SCROLL_MASK)
        self.c_win.connect("key-press-event", self.on_navigation)
        self.c_win.connect("scroll-event", self.on_navigation)

        # Presenter window
        self.p_win.set_title("pympress presenter")
        self.p_win.set_default_size(800, 600)
        self.p_win.set_position(gtk.WIN_POS_CENTER)
        self.p_win.connect("delete-event", gtk.main_quit)
        self.p_win.set_icon_list(*icon_list)

        screen = self.p_win.get_screen()
        if screen.get_n_monitors() > 1:
            cx, cy, cw, ch = self.c_win.get_position() + self.c_win.get_size()
            c_monitor = screen.get_monitor_at_point(cx + cw / 2, cy + ch / 2)
            p_monitor = 0 if c_monitor > 0 else 1

            p_bounds = screen.get_monitor_geometry(p_monitor)
            self.p_win.move(p_bounds.x, p_bounds.y)
            self.p_win.maximize()

            c_bounds = screen.get_monitor_geometry(c_monitor)
            self.c_win.move(c_bounds.x, c_bounds.y)
            self.c_win.fullscreen()
            self.fullscreen = True

        # Document
        self.doc = doc

        # Put Menu and Table in VBox
        bigvbox = gtk.VBox(False, 2)
        self.p_win.add(bigvbox)

        # UI Manager for menu
        ui_manager = gtk.UIManager()

        # UI description
        ui_desc = '''
        <menubar name="MenuBar">
          <menu action="File">
            <menuitem action="Quit"/>
          </menu>
          <menu action="Presentation">
            <menuitem action="Pause timer"/>
            <menuitem action="Reset timer"/>
            <menuitem action="Fullscreen"/>
            <menuitem action="Swap screens"/>
            <menuitem action="Notes mode"/>
          </menu>
          <menu action="Navigation">
            <menuitem action="Next"/>
            <menuitem action="Previous"/>
            <menuitem action="First"/>
            <menuitem action="Last"/>
            <menuitem action="Go to..."/>
          </menu>
          <menu action="Help">
            <menuitem action="About"/>
          </menu>
        </menubar>'''
        ui_manager.add_ui_from_string(ui_desc)

        # Accelerator group
        accel_group = ui_manager.get_accel_group()
        self.p_win.add_accel_group(accel_group)

        # Action group
        action_group = gtk.ActionGroup("MenuBar")
        # Name, stock id, label, accelerator, tooltip, action [, is_active]
        action_group.add_actions([
            ("File",         None,           "_File"),
            ("Presentation", None,           "_Presentation"),
            ("Navigation",   None,           "_Navigation"),
            ("Help",         None,           "_Help"),

            ("Quit",         gtk.STOCK_QUIT, "_Quit",        "q",     None, gtk.main_quit),
            ("Reset timer",  None,           "_Reset timer", "r",     None, self.reset_timer),
            ("About",        None,           "_About",       None,    None, self.menu_about),
            ("Swap screens", None,           "_Swap screens","s",     None, self.swap_screens),

            ("Next",         None,           "_Next",        "Right", None, self.doc.goto_next),
            ("Previous",     None,           "_Previous",    "Left",  None, self.doc.goto_prev),
            ("First",        None,           "_First",       "Home",  None, self.doc.goto_home),
            ("Last",         None,           "_Last",        "End",   None, self.doc.goto_end),
            ("Go to...",     None,           "_Go to...",    "g",     None, self.on_label_event),
        ])
        action_group.add_toggle_actions([
            ("Pause timer",  None,           "_Pause timer", "p",     None, self.switch_pause,      True),
            ("Fullscreen",   None,           "_Fullscreen",  "f",     None, self.switch_fullscreen, self.fullscreen),
            ("Notes mode",   None,           "_Note mode",   "n",     None, self.switch_mode,       self.notes_mode),
        ])
        ui_manager.insert_action_group(action_group)

        # Add menu bar to the window
        menubar = ui_manager.get_widget('/MenuBar')
        h = ui_manager.get_widget('/MenuBar/Help')
        h.set_right_justified(True)
        bigvbox.pack_start(menubar, False)

        # A little space around everything in the window
        align = gtk.Alignment(0.5, 0.5, 1, 1)
        align.set_padding(5, 5, 5, 5)

        # Table
        table = gtk.Table(2, 10, False)
        table.set_col_spacings(5)
        table.set_row_spacings(5)
        align.add(table)
        bigvbox.pack_end(align)

        # "Current slide" frame
        frame = gtk.Frame("Current slide")
        table.attach(frame, 0, 7, 0, 1)
        align = gtk.Alignment(0.5, 0.5, 1, 1)
        align.set_padding(0, 0, 0, 0)
        frame.add(align)
        align.add(self.p_frame_cur)
        self.p_da_cur.modify_bg(gtk.STATE_NORMAL, black)
        self.p_da_cur.connect("expose-event", self.on_expose)
        self.p_da_cur.set_name("p_da_cur")
        if self.notes_mode:
            self.cache.add_widget("p_da_cur", PDF_NOTES_PAGE)
        else :
            self.cache.add_widget("p_da_cur", PDF_REGULAR)
        self.p_da_cur.connect("configure-event", self.on_configure)
        self.p_frame_cur.add(self.p_da_cur)

        # "Next slide" frame
        frame = gtk.Frame("Next slide")
        frame.add(self.p_frame_next)
        align = gtk.Alignment(0.5, 0, 1, 0.5)
        align.set_padding(0, 0, 0, 0)
        align.add(frame)
        table.attach(align, 7, 10, 0, 1)
        self.p_da_next.modify_bg(gtk.STATE_NORMAL, black)
        self.p_da_next.connect("expose-event", self.on_expose)
        self.p_da_next.set_name("p_da_next")
        if self.notes_mode:
            self.cache.add_widget("p_da_next", PDF_CONTENT_PAGE)
        else :
            self.cache.add_widget("p_da_next", PDF_REGULAR)
        self.p_da_next.connect("configure-event", self.on_configure)
        self.p_frame_next.add(self.p_da_next)

        # "Current slide" label and entry. This is ugly.
        # We have EventBox eb_cur around HBox hb_cur containing 4 Labels
        # And Label label_cur can be swapped for a SpinButton spin_cur :
        # [[ [anonymous spacer] [label_cur|spin_cur] [label_last] [anonymous spacer] ]]
        self.label_cur.set_justify(gtk.JUSTIFY_RIGHT)
        self.label_cur.set_use_markup(True)
        self.label_last.set_justify(gtk.JUSTIFY_LEFT)
        self.label_last.set_use_markup(True)
        self.hb_cur=gtk.HBox()
        self.hb_cur.pack_start(self.label_cur)
        self.hb_cur.pack_start(self.label_last)
        align = gtk.Alignment(0.5, 0.5, 0, 0)
        align.set_padding(0, 0, 0, 0)
        align.add(self.hb_cur)
        self.eb_cur.add(align)
        self.spin_cur = SlideSelector(self, doc.pages_number())
        self.spin_cur.set_alignment(0.5)
        self.spin_cur.modify_font(pango.FontDescription('36'))

        self.eb_cur.set_visible_window(False)
        self.eb_cur.connect("event", self.on_label_event)
        frame = gtk.Frame("Slide number")
        frame.add(self.eb_cur)
        table.attach(frame, 0, 3, 1, 2, yoptions=gtk.FILL)

        # "Time elapsed" frame
        frame = gtk.Frame("Time elapsed")
        table.attach(frame, 3, 8, 1, 2, yoptions=gtk.FILL)
        align = gtk.Alignment(0.5, 0.5, 1, 1)
        align.set_padding(10, 10, 12, 0)
        frame.add(align)
        self.label_time.set_use_markup(True)
        self.label_time.set_justify(gtk.JUSTIFY_CENTER)
        self.label_time.set_width_chars(44) # close enough to 13 characters at font size 36
        align.add(self.label_time)

        # "Clock" frame
        frame = gtk.Frame("Clock")
        table.attach(frame, 8, 10, 1, 2, yoptions=gtk.FILL)
        align = gtk.Alignment(0.5, 0.5, 1, 1)
        align.set_padding(10, 10, 12, 0)
        frame.add(align)
        self.label_clock.set_justify(gtk.JUSTIFY_CENTER)
        self.label_clock.set_use_markup(True)
        align.add(self.label_clock)

        self.p_win.connect("destroy", gtk.main_quit)
        self.p_win.show_all()


        # Add events
        self.p_win.add_events(gtk.gdk.KEY_PRESS_MASK | gtk.gdk.SCROLL_MASK)
        self.p_win.connect("key-press-event", self.on_navigation)
        self.p_win.connect("scroll-event", self.on_navigation)

        # Hyperlinks if available
        if pympress.util.poppler_links_available():
            self.c_da.add_events(gtk.gdk.BUTTON_PRESS_MASK | gtk.gdk.POINTER_MOTION_MASK)
            self.c_da.connect("button-press-event", self.on_link)
            self.c_da.connect("motion-notify-event", self.on_link)

            self.p_da_cur.add_events(gtk.gdk.BUTTON_PRESS_MASK | gtk.gdk.POINTER_MOTION_MASK)
            self.p_da_cur.connect("button-press-event", self.on_link)
            self.p_da_cur.connect("motion-notify-event", self.on_link)

            self.p_da_next.add_events(gtk.gdk.BUTTON_PRESS_MASK | gtk.gdk.POINTER_MOTION_MASK)
            self.p_da_next.connect("button-press-event", self.on_link)
            self.p_da_next.connect("motion-notify-event", self.on_link)

        # Setup timer
        gobject.timeout_add(250, self.update_time)

        # Show all windows
        self.c_win.show_all()
        self.p_win.show_all()


    def run(self):
        """Run the GTK main loop."""
        with gtk.gdk.lock:
            gtk.main()


    def menu_about(self, widget=None, event=None):
        """Display the "About pympress" dialog."""
        about = gtk.AboutDialog()
        about.set_program_name("pympress")
        about.set_version(pympress.__version__)
        about.set_copyright("(c) 2009, 2010 Thomas Jost")
        about.set_comments("pympress is a little PDF reader written in Python using Poppler for PDF rendering and GTK for the GUI.")
        about.set_website("http://www.pympress.org/")
        try:
            req = pkg_resources.Requirement.parse("pympress")
            icon_fn = pkg_resources.resource_filename(req, "share/pixmaps/pympress-128.png")
            about.set_logo(gtk.gdk.pixbuf_new_from_file(icon_fn))
        except Exception, e:
            print(e)
        about.run()
        about.destroy()


    def page_preview(self, page_nb):
        """
        Switch to another page and display it.

        This is a kind of event which is supposed to be called only from the
        :class:`~pympress.document.Document` class.

        :param unpause: ``True`` if the page change should unpause the timer,
           ``False`` otherwise
        :type  unpause: boolean
        """
        page_cur = self.doc.page(page_nb)
        page_next = self.doc.page(page_nb+1)

        self.page_preview_nb = page_nb

        # Aspect ratios
        pr = page_cur.get_aspect_ratio(self.notes_mode)
        self.p_frame_cur.set_property("ratio", pr)

        if page_next is not None:
            pr = page_next.get_aspect_ratio(self.notes_mode)
            self.p_frame_next.set_property("ratio", pr)

        # Don't queue draw event but draw directly (faster)
        self.on_expose(self.p_da_cur)
        self.on_expose(self.p_da_next)

        # Prerender the 4 next pages and the 2 previous ones
        cur = page_cur.number()
        page_max = min(self.doc.pages_number(), cur + 5)
        page_min = max(0, cur - 2)
        for p in range(cur+1, page_max) + range(cur, page_min, -1):
            self.cache.prerender(p)


    def on_page_change(self, unpause=True):
        """
        Switch to another page and display it.

        This is a kind of event which is supposed to be called only from the
        :class:`~pympress.document.Document` class.

        :param unpause: ``True`` if the page change should unpause the timer,
           ``False`` otherwise
        :type  unpause: boolean
        """
        page_cur = self.doc.current_page()
        page_next = self.doc.next_page()

        # Page change: resynchronize miniatures
        self.page_preview_nb = page_cur.number()

        # Aspect ratios
        pr = page_cur.get_aspect_ratio(self.notes_mode)
        self.c_frame.set_property("ratio", pr)
        self.p_frame_cur.set_property("ratio", pr)

        if page_next is not None:
            pr = page_next.get_aspect_ratio(self.notes_mode)
            self.p_frame_next.set_property("ratio", pr)

        # Start counter if needed
        if unpause:
            self.paused = False
            if self.start_time == 0:
                self.start_time = time.time()

        # Update display
        self.update_page_numbers()

        # Don't queue draw event but draw directly (faster)
        self.on_expose(self.c_da)
        self.on_expose(self.p_da_cur)
        self.on_expose(self.p_da_next)

        # Prerender the 4 next pages and the 2 previous ones
        page_max = min(self.doc.pages_number(), self.page_preview_nb + 5)
        page_min = max(0, self.page_preview_nb - 2)
        for p in range(self.page_preview_nb+1, page_max) + range(self.page_preview_nb, page_min, -1):
            self.cache.prerender(p)


    def on_expose(self, widget, event=None):
        """
        Manage expose events for both windows.

        This callback may be called either directly on a page change or as an
        event handler by GTK. In both cases, it determines which widget needs to
        be updated, and updates it, using the
        :class:`~pympress.pixbufcache.PixbufCache` if possible.

        :param widget: the widget to update
        :type  widget: :class:`gtk.Widget`
        :param event: the GTK event (or ``None`` if called directly)
        :type  event: :class:`gtk.gdk.Event`
        """

        if widget is self.c_da:
            # Current page
            page = self.doc.page(self.doc.current_page().number())
        elif widget is self.p_da_cur:
            # Current page 'preview'
            page = self.doc.page(self.page_preview_nb)
        else:
            # Next page: it can be None
            page = self.doc.page(self.page_preview_nb+1)
            if page is None:
                widget.hide_all()
                widget.parent.set_shadow_type(gtk.SHADOW_NONE)
                return
            else:
                widget.show_all()
                widget.parent.set_shadow_type(gtk.SHADOW_IN)

        # Instead of rendering the document to a Cairo surface (which is slow),
        # use a pixbuf from the cache if possible.
        name = widget.get_name()
        nb = page.number()
        pb = self.cache.get(name, nb)
        wtype = self.cache.get_widget_type(name)

        if pb is None:
            # Cache miss: render the page, and save it to the cache
            self.render_page(page, widget, wtype)
            ww, wh = widget.window.get_size()
            pb = gtk.gdk.Pixbuf(gtk.gdk.COLORSPACE_RGB, False, 8, ww, wh)
            pb.get_from_drawable(widget.window, widget.window.get_colormap(), 0, 0, 0, 0, ww, wh)
            self.cache.set(name, nb, pb)
        else:
            # Cache hit: draw the pixbuf from the cache to the widget
            gc = widget.window.new_gc()
            widget.window.draw_pixbuf(gc, pb, 0, 0, 0, 0)


    def on_configure(self, widget, event):
        """
        Manage "configure" events for both windows.

        In the GTK world, this event is triggered when a widget's configuration
        is modified, for example when its size changes. So, when this event is
        triggered, we tell the local :class:`~pympress.pixbufcache.PixbufCache`
        instance about it, so that it can invalidate its internal cache for the
        specified widget and pre-render next pages at a correct size.

        :param widget: the widget which has been resized
        :type  widget: :class:`gtk.Widget`
        :param event: the GTK event, which contains the new dimensions of the
           widget
        :type  event: :class:`gtk.gdk.Event`
        """
        self.cache.resize_widget(widget.get_name(), event.width, event.height)


    def on_navigation(self, widget, event):
        """
        Manage events as mouse scroll or clicks for both windows.

        :param widget: the widget in which the event occured (ignored)
        :type  widget: :class:`gtk.Widget`
        :param event: the event that occured
        :type  event: :class:`gtk.gdk.Event`
        """
        if event.type == gtk.gdk.KEY_PRESS:
            name = gtk.gdk.keyval_name(event.keyval)

            # send all to spinner if it is active to avoid key problems
            if self.editing_cur and self.spin_cur.on_keypress(widget, event):
                return True

            if name in ["Right", "Down", "Page_Down", "space"]:
                self.doc.goto_next()
            elif name in ["Left", "Up", "Page_Up", "BackSpace"]:
                self.doc.goto_prev()
            elif name == 'Home':
                self.doc.goto_home()
            elif name == 'End':
                self.doc.goto_end()
            elif (name.upper() in ["F", "F11"]) \
                or (name == "Return" and event.state & gtk.gdk.MOD1_MASK) \
                or (name.upper() == "L" and event.state & gtk.gdk.CONTROL_MASK):
                self.switch_fullscreen()
            elif name.upper() == "Q":
                gtk.main_quit()
            elif name == "Pause":
                self.switch_pause()
            elif name.upper() == "R":
                self.reset_timer()

            # Some key events are already handled by toggle actions in the
            # presenter window, so we must handle them in the content window
            # only to prevent them from double-firing
            if widget is self.c_win:
                if name.upper() == "P":
                    self.switch_pause()
                elif name.upper() == "N":
                    self.switch_mode()
                elif name.upper() == "S":
                    self.swap_screens()
                elif name.upper() == "G":
                    self.on_label_event(self.eb_cur, gtk.gdk.Event(gtk.gdk.BUTTON_PRESS))
                else:
                    return False
                return True
            else:
                return False

            return True

        elif event.type == gtk.gdk.SCROLL:
            if event.direction in [gtk.gdk.SCROLL_RIGHT, gtk.gdk.SCROLL_DOWN]:
                self.doc.goto_next()
            else:
                self.doc.goto_prev()

            return True

        else:
            print("Unknown event " + str(event.type))

        return False


    def on_link(self, widget, event):
        """
        Manage events related to hyperlinks.

        :param widget: the widget in which the event occured
        :type  widget: :class:`gtk.Widget`
        :param event: the event that occured
        :type  event: :class:`gtk.gdk.Event`
        """

        # Where did the event occur?
        if widget is self.p_da_next:
            page = self.doc.next_page()
            if page is None:
                return
        else:
            page = self.doc.current_page()

        # Normalize event coordinates and get link
        x, y = event.get_coords()
        ww, wh = widget.window.get_size()
        x2, y2 = x/ww, y/wh
        link = page.get_link_at(x2, y2)

        # Event type?
        if event.type == gtk.gdk.BUTTON_PRESS:
            if link is not None:
                dest = link.get_destination()
                self.doc.goto(dest)

        elif event.type == gtk.gdk.MOTION_NOTIFY:
            if link is not None:
                cursor = gtk.gdk.Cursor(gtk.gdk.HAND2)
                widget.window.set_cursor(cursor)
            else:
                widget.window.set_cursor(None)

        else:
            print("Unknown event " + str(event.type))


    def on_label_event(self, *args):
        """
        Manage events on the current slide label/entry.

        This function replaces the label with an entry when clicked, replaces
        the entry with a label when needed, etc. The nasty stuff it does is an
        ancient kind of dark magic that should be avoided as much as possible...

        :param widget: the widget in which the event occured
        :type  widget: :class:`gtk.Widget`
        :param event: the event that occured
        :type  event: :class:`gtk.gdk.Event`
        """

        event=args[-1]

        # Click in label-mode
        if self.label_cur in self.hb_cur.children() and (
            type(event) == gtk.Action or
            (type(event) ==gtk.gdk.Event and event.type == gtk.gdk.BUTTON_PRESS)
        ):
            # Replace label with entry
            self.hb_cur.remove(self.label_cur)
            self.spin_cur.show()
            self.hb_cur.add(self.spin_cur)
            self.hb_cur.reorder_child(self.spin_cur, 0)
            self.spin_cur.grab_focus()
            self.editing_cur = True

            self.spin_cur.set_value(self.doc.current_page().number()+1)
            self.spin_cur.select_region(0, -1)

        elif self.editing_cur:
            self.spin_cur.grab_focus()

        # Propagate the event further
        return False



    def render_page(self, page, widget, wtype):
        """
        Render a page on a widget.

        This function takes care of properly initializing the widget so that
        everything looks fine in the end. The rendering to a Cairo surface is
        done using the :meth:`pympress.document.Page.render_cairo` method.

        :param page: the page to render
        :type  page: :class:`pympress.document.Page`
        :param widget: the widget on which the page must be rendered
        :type  widget: :class:`gtk.DrawingArea`
        :param wtype: the type of document to render
        :type  wtype: integer
        """

        # Make sure the widget is initialized
        if widget.window is None:
            return

        # Widget size
        ww, wh = widget.window.get_size()

        # Manual double buffering (since we use direct drawing instead of
        # calling queue_draw() on the widget)
        widget.window.begin_paint_rect(gtk.gdk.Rectangle(0, 0, ww, wh))

        cr = widget.window.cairo_create()
        page.render_cairo(cr, ww, wh, wtype)

        # Blit off-screen buffer to screen
        widget.window.end_paint()


    def restore_current_label(self):
        """
        Make sure that the current page number is displayed in a label and not
        in an entry. If it is an entry, then replace it with the label.
        """
        if self.label_cur not in self.hb_cur.children():
            self.hb_cur.remove(self.spin_cur)
            self.hb_cur.pack_start(self.label_cur, False)
            self.hb_cur.reorder_child(self.label_cur, 0)

        self.editing_cur = False


    def update_page_numbers(self):
        """Update the displayed page numbers."""

        text = "<span font='36'>{}</span>"

        cur_nb = self.doc.current_page().number()
        cur = str(cur_nb+1)
        last = "/{}".format(self.doc.pages_number())

        self.label_cur.set_markup(text.format(cur))
        self.label_last.set_markup(text.format(last))
        self.restore_current_label()


    def update_time(self):
        """
        Update the timer and clock labels.

        :return: ``True`` (to prevent the timer from stopping)
        :rtype: boolean
        """

        # Current time
        clock = time.strftime("%H:%M:%S")

        # Time elapsed since the beginning of the presentation
        if not self.paused:
            self.delta = time.time() - self.start_time
        elapsed = "{:02}:{:02}".format(int(self.delta/60), int(self.delta%60))
        if self.paused:
            elapsed += " (pause)"

        self.label_time.set_markup("<span font='36'>{}</span>".format(elapsed))
        self.label_clock.set_markup("<span font='24'>{}</span>".format(clock))

        return True


    def switch_pause(self, widget=None, event=None):
        """Switch the timer between paused mode and running (normal) mode."""
        if self.paused:
            self.start_time = time.time() - self.delta
            self.paused = False
        else:
            self.paused = True
        self.update_time()


    def reset_timer(self, widget=None, event=None):
        """Reset the timer."""
        self.start_time = time.time()
        self.update_time()


    def set_screensaver(self, must_disable):
        """
        Enable or disable the screensaver.

        .. warning:: At the moment, this is only supported on POSIX systems
           where :command:`xdg-screensaver` is installed and working. For now,
           this feature has only been tested on **Linux with xscreensaver**.

        :param must_disable: if ``True``, indicates that the screensaver must be
           disabled; otherwise it will be enabled
        :type  must_disable: boolean
        """
        if os.name == 'posix':
            # On Linux, set screensaver with xdg-screensaver
            # (compatible with xscreensaver, gnome-screensaver and ksaver or whatever)
            cmd = "suspend" if must_disable else "resume"
            status = os.system("xdg-screensaver {} {}".format(cmd, self.c_win.window.xid))
            if status != 0:
                print("Warning: Could not set screensaver status: got status "+str(status), file=sys.stderr)

            # Also manage screen blanking via DPMS
            if must_disable:
                # Get current DPMS status
                pipe = os.popen("xset q") # TODO: check if this works on all locales
                dpms_status = "Disabled"
                for line in pipe.readlines():
                    if line.count("DPMS is") > 0:
                        dpms_status = line.split()[-1]
                        break
                pipe.close()

                # Set the new value correctly
                if dpms_status == "Enabled":
                    self.dpms_was_enabled = True
                    status = os.system("xset -dpms")
                    if status != 0:
                        print("Warning: Could not disable DPMS screen blanking: got status "+str(status), file=sys.stderr)
                else:
                    self.dpms_was_enabled = False

            elif self.dpms_was_enabled:
                # Re-enable DPMS
                status = os.system("xset +dpms")
                if status != 0:
                    print("Warning: Could not enable DPMS screen blanking: got status "+str(status), file=sys.stderr)
        else:
            print("Warning: Unsupported OS: can't enable/disable screensaver", file=sys.stderr)


    def switch_fullscreen(self, widget=None, event=None):
        """
        Switch the Content window to fullscreen (if in normal mode) or to normal
        mode (if fullscreen).

        Screensaver will be disabled when entering fullscreen mode, and enabled
        when leaving fullscreen mode.
        """
        if self.fullscreen:
            self.c_win.unfullscreen()
            self.fullscreen = False
        else:
            self.c_win.fullscreen()
            self.fullscreen = True

        self.set_screensaver(self.fullscreen)


    def swap_screens(self, widget=None, event=None):
        """
        Swap the monitors on which each window is displayed (if there are 2 monitors at least)
        """
        screen = self.p_win.get_screen()
        if screen.get_n_monitors() > 1:
            cx, cy, cw, ch = self.c_win.get_position() + self.c_win.get_size()
            px, py, pw, ph = self.p_win.get_position() + self.p_win.get_size()
            p_monitor = screen.get_monitor_at_point(px + pw / 2, py + ph / 2)
            c_monitor = screen.get_monitor_at_point(cx + cw / 2, cy + ch / 2)

            if p_monitor == c_monitor:
                return

            p_monitor, c_monitor = (c_monitor, p_monitor)

            p_bounds = screen.get_monitor_geometry(p_monitor)
            if self.p_win.maximize_initially:
                self.p_win.unmaximize()
                self.p_win.move(p_bounds.x + (p_bounds.width - pw) / 2, p_bounds.y + (p_bounds.height - ph) / 2)
                self.p_win.maximize()
            else:
                self.p_win.move(p_bounds.x + (p_bounds.width - pw) / 2, p_bounds.y + (p_bounds.height - ph) / 2)

            c_bounds = screen.get_monitor_geometry(c_monitor)
            if self.fullscreen:
                self.c_win.unfullscreen()
                self.c_win.move(c_bounds.x + (c_bounds.width - cw) / 2, c_bounds.y + (c_bounds.height - ch) / 2)
                self.c_win.fullscreen()
            else:
                self.c_win.move(c_bounds.x + (c_bounds.width - cw) / 2, c_bounds.y + (c_bounds.height - ch) / 2)

        self.on_page_change(False)


    def switch_mode(self, widget=None, event=None):
        """
        Switch the display mode to "Notes mode" or "Normal mode" (without notes)
        """
        if self.notes_mode:
            self.notes_mode = False
            self.cache.set_widget_type("c_da", PDF_REGULAR)
            self.cache.set_widget_type("p_da_cur", PDF_REGULAR)
            self.cache.set_widget_type("p_da_next", PDF_REGULAR)
        else:
            self.notes_mode = True
            self.cache.set_widget_type("c_da", PDF_CONTENT_PAGE)
            self.cache.set_widget_type("p_da_cur", PDF_NOTES_PAGE)
            self.cache.set_widget_type("p_da_next", PDF_CONTENT_PAGE)

        self.on_page_change(False)


##
# Local Variables:
# mode: python
# indent-tabs-mode: nil
# py-indent-offset: 4
# fill-column: 80
# end:
