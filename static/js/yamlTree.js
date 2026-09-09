// Renders a plain JS value tree (the same JSON-safe dict the backend also
// dumps to YAML) as nested, collapsible <ul>/<li> markup. No templating
// library -- this mirrors cardTemplate.js's "build an HTML string" approach,
// since Alpine has no recursive-component primitive. Expand/collapse is
// handled entirely via a delegated click listener toggling a `.collapsed`
// class (see resourcePanel.js's handleTreeClick), not Alpine state, so this
// function only needs to produce the fully-expanded markup once per load.

function isContainer(value) {
  return value !== null && typeof value === 'object';
}

function isEmptyContainer(value) {
  if (Array.isArray(value)) return value.length === 0;
  if (isContainer(value)) return Object.keys(value).length === 0;
  return false;
}

function formatScalar(value) {
  if (value === null || value === undefined) return 'null';
  if (typeof value === 'boolean' || typeof value === 'number') return String(value);
  return escapeHtml(value);
}

function leafLiteral(value) {
  if (Array.isArray(value)) return '[]';
  if (isContainer(value)) return '{}';
  return formatScalar(value);
}

function valueModifierClass(value) {
  if (Array.isArray(value) || isContainer(value)) return 'krw-tree-value--empty';
  if (value === null || value === undefined) return 'krw-tree-value--null';
  if (typeof value === 'boolean') return 'krw-tree-value--bool';
  if (typeof value === 'number') return 'krw-tree-value--number';
  if (typeof value === 'string' && value.includes('\n')) return 'krw-tree-value--string krw-tree-value--multiline';
  return 'krw-tree-value--string';
}

function renderLeafRow(labelHtml, value) {
  return `
    <li class="krw-tree-node">
      <div class="krw-tree-row">
        <span class="krw-tree-caret-spacer"></span>
        ${labelHtml}
        <span class="krw-tree-value ${valueModifierClass(value)}">${leafLiteral(value)}</span>
      </div>
    </li>
  `;
}

function renderContainerRow(labelHtml, childrenHtml) {
  return `
    <li class="krw-tree-node">
      <div class="krw-tree-row" data-toggle>
        <span class="krw-tree-caret"></span>
        ${labelHtml}
      </div>
      <ul class="krw-tree-children">${childrenHtml}</ul>
    </li>
  `;
}

function renderYamlValue(labelHtml, value) {
  if (isContainer(value) && !isEmptyContainer(value)) {
    const childrenHtml = Array.isArray(value)
      ? value.map((item) => renderYamlValue('<span class="krw-tree-key krw-tree-key--index">-</span>', item)).join('')
      : renderYamlEntries(value);
    return renderContainerRow(labelHtml, childrenHtml);
  }
  return renderLeafRow(labelHtml, value);
}

function renderYamlEntries(obj) {
  return Object.entries(obj)
    .map(([key, value]) => renderYamlValue(`<span class="krw-tree-key">${escapeHtml(key)}:</span>`, value))
    .join('');
}

function renderYamlTree(data) {
  if (!isContainer(data)) return '';
  return `<ul class="krw-tree-root">${renderYamlEntries(data)}</ul>`;
}
