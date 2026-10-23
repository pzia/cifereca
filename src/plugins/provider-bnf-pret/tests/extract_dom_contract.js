// Execute the production extraction function against a minimal synthetic DOM.
// This is a JavaScript unit test, not a replacement for Chromium acceptance.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const extract = new Function('return (' + fs.readFileSync(0, 'utf8') + ')')();
global.Node = {ELEMENT_NODE: 1, TEXT_NODE: 3};
global.location = {href: 'https://pret.bnf.fr/my_profile/activity'};
global.getComputedStyle = node => ({display: node.tagName === 'META' ? 'none' : node.display || 'block',
                                  visibility: 'visible'});
function text(value) { return {nodeType: 3, textContent: value}; }
function el(tag, attrs = {}, children = []) {
  return {nodeType: 1, tagName: tag.toUpperCase(), childNodes: children,
    hidden: Object.hasOwn(attrs, 'hidden'),
    classList: (attrs.class || '').split(/\s+/),
    getAttribute: key => attrs[key] ?? null,
    hasAttribute: key => Object.hasOwn(attrs, key)};
}
let roots = [];
global.document = {querySelectorAll: () => roots};
const args = {kind: 'loans', maxBytes: 524288, maxNodes: 12000, maxDepth: 64};
const loan = el('div', {class: 'js-loan private-account-name', onclick: 'PRIVATE_SCRIPT'}, [
  text('PRIVATE_READER_NAME'),
  el('input', {name: 'SAMLResponse', value: 'PRIVATE_SAML'}),
  el('a', {href: '/loans/synthetic-loan?signature=PRIVATE_SIGNATURE'}, [text('PRIVATE_TITLE')]),
  el('a', {href: '/resources/synthetic-book'}, []),
]);
roots = [el('section', {class: 'active-loans'}, [loan])];
let result = extract(args);
assert.equal(result.code, 'ok');
assert.ok(result.html.includes('href="/loans/synthetic-loan"'));
assert.ok(!JSON.stringify(result).includes('PRIVATE_'));
assert.ok(!result.html.includes('onclick'));

global.location.href = 'https://pret.bnf.fr/resources/synthetic-book';
roots = [el('article', {itemscope: '', itemtype: 'https://schema.org/Book'}, [
  el('h1', {itemprop: 'name'}, [text('A <synthetic> book')]),
  el('meta', {itemprop: 'isbn', content: '9780000000002'}),
  el('script', {}, [text('PRIVATE_SCRIPT')]),
  el('textarea', {}, [text('PRIVATE_FORM')]),
  el('div', {hidden: ''}, [text('PRIVATE_HIDDEN')]),
  el('a', {href: 'https://evil.invalid/resources/synthetic'}, [text('Reference')]),
])];
result = extract({...args, kind:'detail'});
assert.equal(result.code, 'ok');
assert.ok(result.html.includes('content="9780000000002"')); // meta display:none is legitimate microdata
assert.ok(result.html.includes('A &lt;synthetic&gt; book'));
assert.ok(!result.html.includes('PRIVATE_'));
assert.ok(!result.html.includes('evil.invalid'));
assert.equal(extract({...args, kind:'detail', maxNodes:2}).code, 'dom_limit');
assert.equal(extract({...args, kind:'detail', maxBytes:30}).code, 'html_limit');
assert.equal(extract({...args, kind:'detail', maxDepth:0}).code, 'dom_limit');
roots = [el('article', {}, [text('x'.repeat(524289))])];
assert.equal(extract({...args, kind:'detail'}).code, 'html_limit');
roots = [];
assert.equal(extract(args).code, 'unrecognized_dom');
roots = [loan, loan];
assert.equal(extract(args).code, 'unrecognized_dom');
console.log('extraction_js_contract_ok');
