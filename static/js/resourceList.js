const LEFT_PANE_MIN_WIDTH = 180;
// Chrome around the label text inside one .krw-type-row: icon width + the
// icon/text gap + left/right padding + the selected-row left border, plus a
// small buffer for the fact that canvas measureText and actual DOM text
// layout are never guaranteed to agree to the sub-pixel.
const LEFT_PANE_ROW_CHROME = 16 + 10 + 16 + 16 + 3 + 8;

function resourceList() {
  return {
    resizing: false,
    // Every group starts collapsed on each fresh load (deliberately not
    // persisted, unlike leftPaneWidth/leftPaneVisible) -- tracks which
    // groups the user has expanded *this session* instead of which are
    // collapsed, so "nothing expanded" naturally means "everything
    // collapsed" without needing to know the group list upfront.
    expandedGroups: [],

    // One level of top-level groups, each holding an ordered list of "rows":
    // either a plain resource-type item, or a nested sub-group (its own
    // collapsible sub-header with its own items, e.g. Cluster's "Cluster
    // Roles"). Consecutive entries sharing a `subgroup` collapse into one
    // sub-group row, same as top-level `group` already does.
    get groupedTypes() {
      const types = this.$store.app.resourceTypes;
      const groups = [];
      let currentGroup = null;
      for (const rt of types) {
        if (!currentGroup || currentGroup.group !== rt.group) {
          currentGroup = { group: rt.group, rows: [] };
          groups.push(currentGroup);
        }
        if (rt.subgroup) {
          const lastRow = currentGroup.rows[currentGroup.rows.length - 1];
          if (lastRow && lastRow.subgroup === rt.subgroup) {
            lastRow.items.push(rt);
          } else {
            currentGroup.rows.push({ subgroup: rt.subgroup, items: [rt] });
          }
        } else {
          currentGroup.rows.push({ item: rt });
        }
      }
      return groups;
    },

    select(id) {
      this.$dispatch('select-type', id);
    },

    // `key` is a top-level group name (e.g. "Cluster") or a nested
    // sub-group's compound key (e.g. "Cluster/Cluster Roles") -- both are
    // just opaque strings here, tracked the same way.
    isGroupCollapsed(key) {
      return !this.expandedGroups.includes(key);
    },

    toggleGroup(key) {
      this.expandedGroups = this.expandedGroups.includes(key)
        ? this.expandedGroups.filter((g) => g !== key)
        : [...this.expandedGroups, key];
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
