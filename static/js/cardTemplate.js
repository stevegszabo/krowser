function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

const HEALTH_LABEL = {
  healthy: 'Healthy',
  progressing: 'Progressing',
  degraded: 'Degraded',
  suspended: 'Suspended',
  unknown: 'Unknown',
};

function renderCard(node) {
  const icon = ICONS[node.icon] || ICONS.default;
  const badges = (node.badges || [])
    .map((b) => `<span class="krw-badge krw-badge-${escapeHtml(b.variant)}">${escapeHtml(b.text)}</span>`)
    .join('');
  const healthLabel = HEALTH_LABEL[node.health] || node.health;

  return `
    <div class="krw-card ${node.is_root ? 'is-root' : ''} ${node.is_selected ? 'is-selected' : ''}">
      <div class="krw-card-icon">${icon}</div>
      <div class="krw-card-body">
        <div class="krw-card-title" title="${escapeHtml(node.name)}">${escapeHtml(node.name)}</div>
        <div class="krw-card-kind">${escapeHtml(node.kind)}</div>
        <div class="krw-card-badges">
          <span class="krw-dot krw-dot-${escapeHtml(node.health)}" title="${escapeHtml(healthLabel)}"></span>
          ${badges}
        </div>
      </div>
    </div>
  `;
}
