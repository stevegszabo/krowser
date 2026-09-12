const LEFT_PANE_MIN_WIDTH = 180;
// Chrome around the label text inside one .krw-type-row: icon width + the
// icon/text gap + left/right padding + the selected-row left border, plus a
// small buffer for the fact that canvas measureText and actual DOM text
// layout are never guaranteed to agree to the sub-pixel.
const LEFT_PANE_ROW_CHROME = 16 + 10 + 16 + 16 + 3 + 8;

function resourceList() {
  return {
    resizing: false,

    get groupedTypes() {
      const types = this.$store.app.resourceTypes;
      const groups = [];
      let current = null;
      for (const rt of types) {
        if (rt.group && current && current.group === rt.group) {
          current.items.push(rt);
        } else {
          current = { group: rt.group, items: [rt] };
          groups.push(current);
        }
      }
      return groups;
    },

    select(id) {
      this.$dispatch('select-type', id);
    },

    // The pane may grow up to whatever width fully displays its longest
    // current resource-type label (e.g. a long CRD name) without wrapping.
    longestLabelMaxWidth() {
      const row = this.$el.querySelector('.krw-type-row span:last-child');
      const font = row ? getComputedStyle(row).font : '13.5px sans-serif';
      const canvas = (this._measureCanvas ||= document.createElement('canvas'));
      const ctx = canvas.getContext('2d');
      ctx.font = font;
      const longest = this.$store.app.resourceTypes.reduce(
        (max, rt) => Math.max(max, ctx.measureText(rt.label).width),
        0
      );
      const maxWidth = longest + LEFT_PANE_ROW_CHROME;
      return Math.min(maxWidth, window.innerWidth - 200);
    },

    startResize(evt) {
      evt.preventDefault();
      const startX = evt.clientX;
      const startWidth = this.$store.app.leftPaneWidth;
      const maxWidth = Math.max(LEFT_PANE_MIN_WIDTH, this.longestLabelMaxWidth());
      this.resizing = true;
      document.body.style.userSelect = 'none';

      const onMouseMove = (moveEvt) => {
        // The handle sits on the pane's right edge, so dragging right
        // (positive clientX delta) should widen the pane.
        const delta = moveEvt.clientX - startX;
        const newWidth = Math.min(Math.max(startWidth + delta, LEFT_PANE_MIN_WIDTH), maxWidth);
        this.$store.app.leftPaneWidth = newWidth;
      };

      const onMouseUp = () => {
        this.resizing = false;
        document.body.style.userSelect = '';
        document.removeEventListener('mousemove', onMouseMove);
        document.removeEventListener('mouseup', onMouseUp);
        try {
          localStorage.setItem('krowser.leftPaneWidth', String(this.$store.app.leftPaneWidth));
        } catch (_) {
          // localStorage unavailable (private browsing, etc.) -- width just won't persist.
        }
      };

      document.addEventListener('mousemove', onMouseMove);
      document.addEventListener('mouseup', onMouseUp);
    },
  };
}
