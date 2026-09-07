function resourceList() {
  return {
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
  };
}
