"""Piano roll side bar: the song's versions and a two-column comparison against one of them."""

import tkinter as tk
from tkinter import font as tkfont

from gui_i18n import tr

SIDEBAR_WIDTH = 290
SIDEBAR_BACKGROUND = '#f4f5f7'
SELECTED_ROW = '#dde6f5'
HOVER_ROW = '#e8ebef'
ACCENT = '#2f6fdb'
PRIMARY_TEXT = '#1f2937'
SECONDARY_TEXT = '#6b7685'
DISABLED_TEXT = '#a3abb6'
DIVIDER = '#dfe3e8'
# Comparison colours, shared with the roll's overlay and legend.
DELETED_FILL, DELETED_OUTLINE = '#f4b8b8', '#bd6262'
SHORTENED_FILL, SHORTENED_OUTLINE = '#f4cf9b', '#c28a3c'
LENGTHENED_OUTLINE = '#2f9483'
ANSWER_ERROR = '#d32f2f'

STAGE_LABELS = (('clean', 'Rule cleaning'), ('playable', 'Playable reduction'), ('trim', 'Overlap trim'))
PADDING = 12
ROW_HEIGHT = 42
STAGE_HEIGHT = 18
KIND_HEIGHT = 26


def draw_swatch(canvas, kind, x, middle, enabled=True, tags=()):
    """A small note-shaped sample of how the roll draws one comparison kind.

    The shown file's own notes and tails are outlined; what only the reference has is a pale ghost.
    """
    top, bottom = middle - 4, middle + 4
    if not enabled:
        return canvas.create_rectangle(x, top, x + 14, bottom, outline=DISABLED_TEXT, tags=tags)
    if kind == 'reference_only':
        return canvas.create_rectangle(x, top, x + 14, bottom, fill=DELETED_FILL, outline=DELETED_OUTLINE,
                                       dash=(3, 2), tags=tags)
    if kind == 'reference_longer':
        return canvas.create_rectangle(x, top, x + 14, bottom, fill=SHORTENED_FILL, outline=SHORTENED_OUTLINE,
                                       tags=tags)
    color = ANSWER_ERROR if kind == 'shown_only' else LENGTHENED_OUTLINE
    return canvas.create_rectangle(x + 1, top, x + 13, bottom, outline=color, width=2, tags=tags)


def rounded_rect(canvas, x0, y0, x1, y1, radius, **options):
    points = (x0 + radius, y0, x1 - radius, y0, x1, y0, x1, y0 + radius, x1, y1 - radius, x1, y1,
              x1 - radius, y1, x0 + radius, y1, x0, y1, x0, y1 - radius, x0, y0 + radius, x0, y0)
    return canvas.create_polygon(points, smooth=True, **options)


class VersionSidebar(tk.Frame):
    """Rows show a version when clicked; each row's badge picks it as the comparison."""

    def __init__(self, master, on_show, on_reference, on_toggle, on_next):
        super().__init__(master, background=SIDEBAR_BACKGROUND, width=SIDEBAR_WIDTH)
        self.on_show, self.on_reference, self.on_toggle, self.on_next = on_show, on_reference, on_toggle, on_next
        family = tkfont.nametofont('TkDefaultFont').actual('family')
        size = tkfont.nametofont('TkDefaultFont').actual('size')
        self.title_font = tkfont.Font(family=family, size=size + 1, weight='bold')
        self.role_font = tkfont.Font(family=family, size=size, weight='bold')
        self.text_font = tkfont.Font(family=family, size=size)
        self.small_font = tkfont.Font(family=family, size=max(9, size - 2))
        self.canvas = tk.Canvas(self, width=SIDEBAR_WIDTH, background=SIDEBAR_BACKGROUND, highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self.state = None
        self.hover = None
        self.canvas.bind('<Configure>', lambda _event: self.redraw())

    def update_view(self, versions, shown_role, reference_role, counts, stages, enabled, message=''):
        self.state = (versions, shown_role, reference_role, counts, stages, enabled, message)
        self.redraw()

    def elide(self, text, font, width, keep_end=False):
        """Shorten text to width; file names keep their end, where versions of one song differ."""
        if font.measure(text) <= width:
            return text
        while text and font.measure(text + '…') > width:
            text = text[1:] if keep_end else text[:-1]
        return '…' + text if keep_end else text + '…'

    def bind_area(self, tag, command):
        self.canvas.tag_bind(tag, '<Enter>', lambda _event: self.set_hover(tag))
        self.canvas.tag_bind(tag, '<Leave>', lambda _event: self.set_hover(None))
        self.canvas.tag_bind(tag, '<Button-1>', lambda _event: command())

    def set_hover(self, tag):
        if self.hover is not None:
            self.canvas.itemconfigure('hover&&' + self.hover, fill=SIDEBAR_BACKGROUND)
        self.hover = tag
        if tag is not None:
            self.canvas.itemconfigure('hover&&' + tag, fill=HOVER_ROW)
        self.canvas.configure(cursor='hand2' if tag is not None else '')

    def redraw(self):
        canvas = self.canvas
        canvas.delete('all')
        self.hover = None
        if self.state is None:
            return
        versions, shown_role, reference_role, counts, stages, enabled, message = self.state
        width = max(SIDEBAR_WIDTH, canvas.winfo_width())
        right = width - PADDING
        y = PADDING
        canvas.create_text(PADDING, y, text=tr('Versions'), anchor='nw', font=self.title_font, fill=PRIMARY_TEXT)
        y += self.title_font.metrics('linespace') + 2
        canvas.create_text(PADDING, y, text=tr('Click to show · Compare sets the other side'), anchor='nw',
                           font=self.small_font, fill=DISABLED_TEXT, width=right - PADDING)
        y += self.small_font.metrics('linespace') + 8

        for index, version in enumerate(versions):
            shown = version.role == shown_role
            row = 'row{}'.format(index)
            badge = 'badge{}'.format(index)
            # Only rows that react to clicks take the hover fill; the shown row keeps its selection.
            rounded_rect(canvas, 6, y, width - 6, y + ROW_HEIGHT - 2, 8,
                         fill=SELECTED_ROW if shown else SIDEBAR_BACKGROUND, outline='',
                         tags=(row, 'hover') if not shown else ())
            dot_color = ACCENT if shown else '#b4bcc7'
            canvas.create_oval(PADDING + 2, y + 12, PADDING + 10, y + 20, fill=dot_color if shown else '',
                               outline=dot_color, width=1.5, tags=row)
            text_left = PADDING + 18
            badge_width = 0
            if not shown:
                selected = version.role == reference_role
                label = tr('Comparing') if selected else tr('Compare')
                badge_width = self.small_font.measure(label) + 14
                x0, y0 = right - badge_width, y + 9
                pill = rounded_rect(canvas, x0, y0, right, y0 + 18, 9, fill=ACCENT if selected else SIDEBAR_BACKGROUND,
                                    outline=ACCENT if selected else '#c3cad3', tags=(badge,))
                if not selected:
                    canvas.addtag_withtag('hover', pill)
                canvas.create_text((x0 + right) / 2, y0 + 9, text=label, font=self.small_font,
                                   fill='white' if selected else SECONDARY_TEXT, tags=badge)
                self.bind_area(badge, lambda role=version.role: self.on_reference(role))
            text_width = right - text_left - badge_width - 6
            canvas.create_text(text_left, y + 6, text=self.elide(version.name, self.role_font, text_width,
                                                                  keep_end=True),
                               anchor='nw', font=self.role_font, fill=PRIMARY_TEXT, tags=row)
            name = version.path.replace('\\', '/').rsplit('/', 1)[-1]
            canvas.create_text(text_left, y + 23, text=self.elide(name, self.small_font, text_width, keep_end=True), anchor='nw',
                               font=self.small_font, fill=SECONDARY_TEXT, tags=row)
            if not shown:
                self.bind_area(row, lambda path=version.path: self.on_show(path))
            y += ROW_HEIGHT
            if version.role == 'cleaned' and stages:
                for stage, label in STAGE_LABELS:
                    if stage not in stages:
                        continue
                    stage_counts = stages[stage]
                    parts = [tr('{} deleted').format(stage_counts['deleted'])] if stage_counts['deleted'] else []
                    if stage_counts['shortened']:
                        parts.append(tr('{} shortened').format(stage_counts['shortened']))
                    canvas.create_text(text_left + 6, y, text=tr(label), anchor='nw', font=self.small_font,
                                       fill=SECONDARY_TEXT)
                    canvas.create_text(right, y, text=tr(', ').join(parts) or '0', anchor='ne',
                                       font=self.small_font, fill=SECONDARY_TEXT)
                    y += STAGE_HEIGHT
                y += 4

        y += 8
        canvas.create_line(PADDING, y, right, y, fill=DIVIDER)
        y += 12
        canvas.create_text(PADDING, y, text=tr('Comparison'), anchor='nw', font=self.title_font, fill=PRIMARY_TEXT)
        y += self.title_font.metrics('linespace') + 4
        if message or reference_role is None:
            canvas.create_text(PADDING, y, text=message or tr('Not comparing. Press Compare on a version.'),
                               anchor='nw', font=self.small_font, fill=SECONDARY_TEXT, width=right - PADDING)
            return

        # One sentence per difference, so a row reads on its own without the roll: a checkbox marks it
        # on the roll, the swatch is how the roll draws it, and Next jumps to the next one.
        reference_name = next(version.name for version in versions if version.role == reference_role)
        current = tr('Current file')
        canvas.create_text(PADDING, y, text=tr('{} against {}').format(current, reference_name), anchor='nw',
                           font=self.small_font, fill=SECONDARY_TEXT, width=right - PADDING)
        y += self.small_font.metrics('linespace') + 10
        canvas.create_text(PADDING, y, text=tr('Mark on the roll:'), anchor='nw', font=self.role_font,
                           fill=PRIMARY_TEXT)
        y += self.role_font.metrics('linespace') + 4
        sentences = (('reference_only', tr('Only in {}').format(reference_name)),
                     ('shown_only', tr('Only in {}').format(current)),
                     ('reference_longer', tr('{} tail longer').format(reference_name)),
                     ('shown_longer', tr('{} tail longer').format(current)))
        next_label = tr('Next')
        next_width = self.small_font.measure(next_label) + 12
        count_right = right - next_width - 8
        for kind, sentence in sentences:
            middle = y + (KIND_HEIGHT - 2) / 2
            present = counts[kind] > 0
            on = enabled[kind] and present
            tag = 'kind_' + kind
            text_color = PRIMARY_TEXT if present else DISABLED_TEXT
            rounded_rect(canvas, 6, y, count_right + 4, y + KIND_HEIGHT - 2, 7, fill=SIDEBAR_BACKGROUND,
                         outline='', tags=(tag, 'hover') if present else ())
            box = (PADDING, middle - 7, PADDING + 14, middle + 7)
            if on:
                rounded_rect(canvas, *box, 3, fill=ACCENT, outline=ACCENT, tags=tag)
                canvas.create_line(box[0] + 3, middle, box[0] + 6, middle + 3, box[0] + 11, middle - 3,
                                   fill='white', width=2, tags=tag)
            else:
                rounded_rect(canvas, *box, 3, fill='white', outline=DISABLED_TEXT if not present else '#9aa3af',
                             tags=tag)
            draw_swatch(canvas, kind, PADDING + 22, middle, tags=tag)
            count_text = str(counts[kind])
            label_width = count_right - self.text_font.measure(count_text) - 8 - (PADDING + 42)
            canvas.create_text(PADDING + 42, middle, text=self.elide(sentence, self.text_font, label_width),
                               anchor='w', font=self.text_font, fill=text_color, tags=tag)
            canvas.create_text(count_right, middle, text=count_text, anchor='e', font=self.text_font,
                               fill=text_color, tags=tag)
            if present:
                self.bind_area(tag, lambda kind=kind: self.on_toggle(kind))
            if on:
                jump = 'next_' + kind
                rounded_rect(canvas, right - next_width, y + 3, right, y + KIND_HEIGHT - 5, 6, fill='white',
                             outline='#c3cad3', tags=(jump,))
                canvas.create_text(right - next_width / 2, middle, text=next_label, font=self.small_font,
                                   fill=ACCENT, tags=jump)
                self.bind_area(jump, lambda kind=kind: self.on_next(kind))
            y += KIND_HEIGHT
