// Pure clipboard/selection logic, kept outside Playwright's e2e discovery.
import assert from 'node:assert/strict';
import test from 'node:test';
import { appendReferenceImages, clipboardImageFiles, commitReferenceSelection, pasteReferenceImages } from '../src/components/clipboardImages.ts';

const image = (name = 'image.png', body = 'pixels', type = 'image/png') => new File([body], name, { type, lastModified: 1 });
const clipboard = (files, withItems = true) => ({ files, items: withItems
  ? files.map(file => ({ kind: 'file', type: file.type, getAsFile: () => file })) : [] });
function paste(data, add, options = {}) {
  let prevented = false;
  pasteReferenceImages({ clipboardData: data, defaultPrevented: !!options.handled,
    preventDefault() { prevented = true; } }, add, !!options.disabled);
  return prevented;
}

test('one paste accepts multiple images exactly once, without duplicating files/items', async () => {
  const files = [image(), image('image.jpg', 'second', 'image/jpeg')];
  const result = clipboardImageFiles(clipboard(files));
  assert.equal(result.length, 2);
  assert.equal(await result[0].text(), 'pixels');
  assert.equal(await result[1].text(), 'second');
  assert.equal(result[1].type, 'image/jpeg');
  assert.match(result[1].name, /\.jpg$/);
});

test('continuous pastes append to the selection and retain existing bytes', async () => {
  const original = image('local.png', 'original');
  let selected = [original];
  const add = files => { selected = appendReferenceImages(selected, files).files; };
  assert.ok(paste(clipboard([image('image.png', 'first')]), add));
  assert.ok(paste(clipboard([image('image.png', 'second'), image('image.png', 'third')]), add));
  assert.equal(selected.length, 4);
  assert.equal(selected[0], original);
  assert.deepEqual(await Promise.all(selected.map(file => file.text())), ['original', 'first', 'second', 'third']);
});

test('different copied pictures with identical browser names/metadata keep distinct identities', () => {
  const result = clipboardImageFiles(clipboard([image(), image()]));
  assert.notEqual(result[0].name, result[1].name);
  const next = clipboardImageFiles(clipboard([image()]));
  assert.notEqual(result[0].name, next[0].name);
});

test('clipboard.files fallback works when image items are unavailable', () => {
  assert.equal(clipboardImageFiles(clipboard([image()], false)).length, 1);
});

test('text, copied image URLs and HTML retain native paste; nothing is fetched or appended', () => {
  const data = { files: [], items: [{ kind: 'string', type: 'text/html', getAsFile: () => null },
    { kind: 'string', type: 'text/plain', getAsFile: () => null }] };
  assert.equal(paste(data, () => assert.fail('should not add URL/HTML')), false);
});

test('missing/null clipboard files do not consume paste', () => {
  assert.equal(paste(null, () => assert.fail('should not add')), false);
  const data = { files: [], items: [{ kind: 'file', type: 'image/png', getAsFile: () => null }] };
  assert.equal(paste(data, () => assert.fail('should not add')), false);
});

test('disabled or already-handled paste does not touch clipboard data', () => {
  const event = { defaultPrevented: false, preventDefault() { assert.fail('should not consume'); },
    get clipboardData() { assert.fail('should not read'); } };
  pasteReferenceImages(event, () => assert.fail('should not add'), true);
  event.defaultPrevented = true;
  pasteReferenceImages(event, () => assert.fail('should not add'));
});

test('20 image boundary accepts the last image then rejects the complete overflow batch', () => {
  const original = Array.from({ length: 19 }, (_, index) => image(`local-${index}.png`));
  const full = appendReferenceImages(original, clipboardImageFiles(clipboard([image()])));
  assert.equal(full.error, '');
  assert.equal(full.files.length, 20);
  const overflow = appendReferenceImages(full.files, clipboardImageFiles(clipboard([image(), image()])));
  assert.equal(overflow.files, full.files);
  assert.match(overflow.error, /最多 20 张/);
});

test('unsupported and empty images preserve the old selection', () => {
  const original = [image('local.png')];
  for (const bad of [image('vector.svg', '<svg/>', 'image/svg+xml'), image('empty.png', '')]) {
    const result = appendReferenceImages(original, [image('valid.png'), bad]);
    assert.equal(result.files, original);
    assert.notEqual(result.error, '');
  }
});

test('4 MiB boundary is accepted, oversized image rejects without partial additions', () => {
  const original = [image('local.png')];
  const exact = new File([new Uint8Array(4 * 1024 * 1024)], 'exact.png', { type: 'image/png' });
  assert.equal(appendReferenceImages(original, [exact]).files.length, 2);
  const large = new File([new Uint8Array(4 * 1024 * 1024 + 1)], 'large.png', { type: 'image/png' });
  const result = appendReferenceImages(original, [image('valid.png'), large]);
  assert.equal(result.files, original);
  assert.match(result.error, /4 MiB/);
});

test('existing local-file metadata duplicate protection stays intact', () => {
  const local = image('local.png');
  const original = [local];
  assert.equal(appendReferenceImages(original, [local, image('local.png')]).files, original);
});

test('parent rejecting a batch leaves no invisible images before the next valid paste', () => {
  const original = [image('local.png')];
  let selected = original;
  const rejected = clipboardImageFiles(clipboard([image(), image()]));
  // Repeat without any simulated rerender, as with an unchanged parent error.
  for (let attempt = 0; attempt < 2; attempt++) {
    const next = appendReferenceImages(selected, rejected);
    selected = commitReferenceSelection(selected, next.files, () => false);
    assert.equal(selected, original);
  }
  const accepted = clipboardImageFiles(clipboard([image()]));
  let parent;
  selected = commitReferenceSelection(selected, appendReferenceImages(selected, accepted).files,
    next => { parent = next; return true; });
  assert.equal(selected, parent);
  assert.deepEqual(selected, [...original, ...accepted]);
});
