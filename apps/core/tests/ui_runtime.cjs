/* Behavioral request-lifecycle tests, using only Node's standard library. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class Element {
  constructor() {
    this.dataset = {};
    this.attrs = {};
    this.classList = {
      values: new Set(),
      add: function (v) { this.values.add(v); },
      remove: function (v) { this.values.delete(v); },
      contains: function (v) { return this.values.has(v); },
      toggle: function () {},
    };
  }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k] || null; }
  removeAttribute(k) { delete this.attrs[k]; }
  matches(selector) { return selector === "form" && this.isForm; }
  closest(selector) { return selector === "form" ? this.form : null; }
}
const documentEvents = {}, windowEvents = {};
const add = (map) => (name, handler) => (map[name] ||= []).push(handler);
const button = new Element();
const form = new Element();
form.isForm = true;
form.dataset.dirty = "true";
form.querySelectorAll = () => button.attrs["aria-busy"] ? [button] : [];
form.querySelector = () => button;
button.form = form;
const body = new Element();
const document = {
  body,
  addEventListener: add(documentEvents),
  querySelectorAll: selector => selector === "form[data-submitting]" && form.dataset.submitting ? [form] : [],
  querySelector: () => null,
  getElementById: () => null,
};
let confirm = true;
const window = {
  addEventListener: add(windowEvents),
  setTimeout: () => 1, clearTimeout: () => {},
  confirm: () => confirm,
  matchMedia: () => ({matches: false}),
};
const context = vm.createContext({window, document, navigator: {}, Element, Set, Array, Boolean, Promise});
const source = fs.readFileSync(process.argv[2], "utf8");
vm.runInContext(source, context);
const listeners = Object.values(documentEvents).reduce((n, list) => n + list.length, 0);
vm.runInContext(source, context);
assert.equal(Object.values(documentEvents).reduce((n, list) => n + list.length, 0), listeners, "HTMX must not duplicate listeners");
function emit(type, extra = {}, map = documentEvents) {
  const event = {target: form, submitter: button, defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; },
    stopImmediatePropagation() {}, ...extra};
  (map[type] || []).forEach(handler => handler(event));
  return event;
}
emit("submit");
assert.equal(form.dataset.submitting, "true");
assert.equal(button.attrs["aria-busy"], "true");
assert.equal(emit("submit").defaultPrevented, true, "duplicate request must be blocked");
emit("htmx:sendError", {detail: {elt: form}});
assert.equal(form.dataset.submitting, undefined);
assert.equal(form.dataset.dirty, "true", "failed request must retain unsaved warning");
assert.equal(button.attrs["aria-busy"], undefined);
assert.equal(emit("submit").defaultPrevented, false, "failed request must be retryable");
emit("htmx:afterRequest", {detail: {elt: form, successful: true}});
assert.equal(form.dataset.submitting, undefined);
assert.equal(form.dataset.dirty, "false");
form.dataset.dirty = "true";
emit("submit");
emit("pageshow", {}, windowEvents);
assert.equal(form.dataset.submitting, undefined, "history restoration must unlock controls");
assert.equal(form.dataset.dirty, "true");
confirm = false;
button.dataset.confirm = "Confirm?";
assert.equal(emit("submit").defaultPrevented, true);
assert.equal(form.dataset.submitting, undefined, "cancelled confirmation must not lock controls");
console.log("UI runtime scenarios passed");

(async function testAssetRefresh() {
  const handlers = {}, cached = new Map();
  let offline = false;
  const response = text => ({ok: true, text, clone() { return response(text); }});
  const worker = vm.createContext({
    URL, Promise,
    self: {location: {origin: "https://sanga.test"}, addEventListener: (name, fn) => handlers[name] = fn},
    caches: {
      open: async () => ({put: async (request, value) => cached.set(request.url, value)}),
      match: async request => cached.get(request.url),
    },
    fetch: async () => { if (offline) throw Error("offline"); return response("fresh CSS"); },
  });
  vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), worker);
  const request = {url: "https://sanga.test/static/css/app.css?v=99", method: "GET", mode: "cors"};
  cached.set(request.url, response("stale CSS"));
  let result;
  const event = {request, respondWith: promise => result = promise};
  handlers.fetch(event);
  assert.equal((await result).text, "fresh CSS", "online navigation must refresh old assets");
  offline = true;
  handlers.fetch(event);
  assert.equal((await result).text, "fresh CSS", "offline fallback must use the updated asset");
  let intercepted = false;
  handlers.fetch({request: {...request, url: "https://sanga.test/app/invoices/"}, respondWith: () => intercepted = true});
  assert.equal(intercepted, false, "private application data must bypass the asset cache");
  console.log("PWA refresh scenarios passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
