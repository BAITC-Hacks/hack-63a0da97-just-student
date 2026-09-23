"use strict";
let csrf = "", busy = false, language = "ru", sessionInfo = {};
const messages = document.querySelector("#messages");
const $ = selector => document.querySelector(selector);
const t = key => translations[language][key] || key;
const el = (tag, text, cls) => {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (cls) node.className = cls;
  return node;
};
function bubble(text, cls = "assistant") {
  const node = el("article", text, "bubble " + cls);
  messages.append(node);
  messages.scrollTop = messages.scrollHeight;
  return node;
}
function link(parent, url, label) {
  try {
    const target = new URL(url, location.origin);
    if (!["http:", "https:"].includes(target.protocol)) return;
    const a = el("a", label);
    a.href = target.href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    parent.append(a);
  } catch { /* Invalid catalog links are not rendered. */ }
}
async function api(path, body, method = "POST") {
  const options = { method, headers: { "X-CSRF-Token": csrf } };
  if (body instanceof FormData) options.body = body;
  else if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try { response = await fetch(path, options); } catch { throw Error(t("error")); }
  const data = await response.json();
  if (!response.ok) throw Error(typeof data.detail === "string" ? data.detail : t("error"));
  return data;
}
async function action(fn) {
  if (busy) return;
  busy = true;
  $("#send").disabled = true;
  $("#notice").textContent = t("waiting");
  try { await fn(); } catch (error) { bubble(error.message, "error"); }
  finally {
    busy = false;
    $("#send").disabled = false;
    $("#notice").textContent = t("notice");
    messages.scrollTop = messages.scrollHeight;
  }
}
function confirmation(proposal) {
  const box = bubble(proposal.answer);
  const yes = el("button", t("yes"), "primary");
  const no = el("button", t("no"), "cancel");
  const decide = confirmed => action(async () => {
    yes.disabled = no.disabled = true;
    render(await api("/api/cart/confirm", { proposal_id: proposal.proposal_id, confirmed }));
  });
  yes.onclick = () => decide(true);
  no.onclick = () => decide(false);
  box.append(el("br"), yes, no);
}
function product(p, reason, cartQuantity, requestedQuantity) {
  const card = el("article", null, "product");
  card.append(el("span", p.supplier_article || p.article || String(p.id), "tag"), el("h3", p.name));
  if (reason) card.append(el("p", reason, "reason"));
  const facts = el("div", null, "facts");
  facts.append(el("span", p.price == null ? t("noPrice") : new Intl.NumberFormat(language === "kk" ? "kk-KZ" : "ru-RU").format(p.price) + " ₸"),
    el("span", p.quantity == null ? t("unknownStock") : p.quantity > 0 ? t("inStock") + p.quantity : t("outStock")));
  card.append(facts);
  const details = el("details");
  details.append(el("summary", t("details")));
  for (const [key, value] of Object.entries(p.characteristics || {})) {
    if (/^(CML2_|BRAND_PRIORITY|NOVINKA|SPETSPREDLOZHENIE|IMYAKARTINKI|OBYEM)/.test(key)) continue;
    const label = propertyLabels[key]?.[language === "kk" ? 1 : 0] || key;
    details.append(el("div", label + ": " + value));
  }
  for (const store of p.stores || []) details.append(el("div", store.name + ": " + store.quantity));
  if (!Object.keys(p.characteristics || {}).length) details.append(el("div", t("noSpecs")));
  if (p.minimum_quantity) details.append(el("div", t("minimum") + p.minimum_quantity));
  if (p.quantity_step) details.append(el("div", t("step") + p.quantity_step));
  card.append(details);
  for (const url of p.certificates || []) link(card, url, t("certificate"));
  if (!p.certificates?.length) card.append(el("p", t("noCertificate"), "reason"));
  for (const warning of p.data_warnings || []) card.append(el("p", (language === "kk" ? "Каталог деректерінде қайшылық бар: " : warning.message + " ") + warning.values.join(", "), "warning"));
  if (p.url) link(card, p.url, "ekt.kz ↗");
  if (cartQuantity != null) card.append(el("p", t("inCart") + cartQuantity));
  else if (p.quantity > 0) {
    const buy = el("div", null, "buy"), count = el("input");
    count.type = "number";
    count.min = String(p.minimum_quantity || 0.001);
    count.step = p.quantity_step || "any";
    count.value = String(requestedQuantity || p.minimum_quantity || 1);
    count.max = p.quantity;
    count.setAttribute("aria-label", t("quantity") + " " + p.name);
    const button = el("button", t("choose"), "primary");
    button.onclick = () => action(async () => {
      if (!count.reportValidity()) return;
      confirmation(await api("/api/cart/propose", { product_id: Number(p.id), quantity: Number(count.value) }));
    });
    buy.append(count, button);
    card.append(buy);
  }
  messages.append(card);
}
function render(data) {
  if (data.answer) bubble(data.answer);
  for (const p of data.products || []) product(p, null, null, data.requested_quantity);
  for (const a of data.alternatives || []) product(a.product, a.reason, null, data.requested_quantity);
  if (data.related?.length) bubble(t("related"));
  for (const r of data.related || []) product(r.product, r.reason);
  if (data.manager_url) link(bubble(t("contacts")), data.manager_url, t("manager"));
  if (data.handoff_url) link(bubble(t("handoff")), data.handoff_url, t("handoff"));
  for (const source of data.sources || []) link(bubble(t("source") + source.title + " · " + source.checked_at), source.url, source.title);
  for (const item of data.items || []) {
    if (item.product) continue;
    bubble(item.source.raw_text);
    for (const p of item.products || []) product(p, null, null, item.source.quantity);
    for (const a of item.alternatives || []) product(a.product, a.reason, null, item.source.quantity);
  }
  if (data.unresolved?.length) bubble(t("unresolved") + data.unresolved.join("; "));
  if (data.truncated) bubble(t("partial"));
  if (data.cart_url) link(bubble(t("cart")), data.cart_url, t("openCart"));
  if (data.coverage && !data.coverage.complete && data.coverage.products) bubble(t("cataloguePartial") + data.coverage.products, "meta");
}
function translatePage() {
  document.documentElement.lang = language;
  $("#language").value = language;
  $("#title").textContent = t(location.pathname === "/cart" ? "cart" : "title");
  $("#message").placeholder = t("placeholder");
  $("#send").setAttribute("aria-label", t("send"));
  $("#message").setAttribute("aria-label", t("placeholder"));
  $("#mode").textContent = t(sessionInfo.demo ? "demo" : "live");
  $(".privacy").textContent = t("privacy");
  $(".cart-link").textContent = t("cartLink");
  $("#notice").textContent = t("notice");
  const labels = {"account-open":"account", "account-title":"accountTitle", "account-note":"accountNote", "username-label":"username", "password-label":"password", login:"login", register:"register", "account-close":"close", logout:"logout", "account-delete":"deleteAccount", "history-clear":"clearHistory", forget:"forget", manager:"manager"};
  for (const [id,key] of Object.entries(labels)) $("#" + id).textContent = t(key);
  if (sessionInfo.username) $("#account-open").textContent = sessionInfo.username;
  const buttons = $("#suggestions").querySelectorAll("button");
  ["products","cable","terms"].forEach((key,i) => buttons[i].textContent = t(key));
  buttons[2].dataset.query = t("terms");
  $(".attach").title = language === "kk" ? "Файл тіркеу" : "Прикрепить файл";
  if (language === "kk") {
    $("h1").textContent = t("heading");
    $(".intro").textContent = "Тауарды табамыз, қалдығын тексереміз және техникалық сипаттамаларын салыстырамыз.";
    $(".eyebrow").textContent = "КАТАЛОГ БОЙЫНША КӨМЕКШІҢІЗ";
    $(".header-note").textContent = "Электротехника. Түсінікті таңдау.";
    $(".steps").replaceChildren(...["01 · Не іздеп жатқаныңызды айтыңыз", "02 · Тауарларды салыстырыңыз", "03 · Қосуды растаңыз"].map(s => el("p", s)));
    $(".chat-heading p").textContent = "Каталог · сипаттамалар · іріктеу";
    $("footer span").textContent = "Техникалық іріктеуді маман тексеруі керек";
  }
}
async function send(text) {
  bubble(text, "user");
  render(await api("/api/chat", { message: text, language }));
}
$("#composer").onsubmit = event => {
  event.preventDefault();
  const input = $("#message"), text = input.value.trim();
  if (!text || busy) return;
  input.value = "";
  action(() => send(text));
};
$("#language").onchange = event => action(async () => {
  language = event.target.value;
  await api("/api/language", { language });
  location.reload();
});
document.querySelectorAll("[data-query]").forEach(button => button.onclick = () => action(() => send(button.dataset.query)));
$("#file").onchange = event => {
  const file = event.target.files[0];
  if (!file) return;
  action(async () => {
    if (file.size > 10 * 1024 * 1024) throw Error(t("maxFile"));
    bubble(t("file") + file.name, "user");
    const data = new FormData();
    data.append("file", file);
    render(await api("/api/chat/attachment", data));
  });
  event.target.value = "";
};
$("#forget").onclick = () => action(async () => { await api("/api/session", undefined, "DELETE"); location.href = "/"; });
$("#history-clear").onclick = () => action(async () => { await api("/api/history", undefined, "DELETE"); messages.replaceChildren(); });
$("#manager").onclick = () => action(() => send("менеджер"));
$("#account-open").onclick = () => {
  $("#account-form").hidden = !!sessionInfo.username;
  $("#account-signed").hidden = !sessionInfo.username;
  $("#account-name").textContent = sessionInfo.username || "";
  $("#account-dialog").showModal();
};
$("#account-close").onclick = () => $("#account-dialog").close();
$("#account-form").onsubmit = async event => {
  event.preventDefault();
  if (busy) return;
  busy = true;
  $("#account-error").textContent = "";
  try {
    await api("/api/account/" + event.submitter.value, { username: $("#username").value, password: $("#password").value });
    $("#password").value = "";
    location.reload();
  } catch (error) { $("#account-error").textContent = error.message; }
  finally { busy = false; }
};
$("#logout").onclick = () => action(async () => { await api("/api/session", undefined, "DELETE"); location.reload(); });
$("#account-delete").onclick = () => action(async () => {
  if (!confirm(t("deleteAsk"))) return;
  await api("/api/account", undefined, "DELETE");
  location.reload();
});
action(async () => {
  sessionInfo = await api("/api/session", undefined, "GET");
  csrf = sessionInfo.csrf;
  language = sessionInfo.language;
  translatePage();
  if (!sessionInfo.demo) {
    const buttons = $("#suggestions").querySelectorAll("button");
    buttons[0].dataset.query = "027228";
    buttons[1].dataset.query = language === "kk" ? "3x2.5 кабель керек" : "Кабель 3x2.5";
  }
  if (location.pathname === "/cart") {
    messages.replaceChildren();
    $("#composer").hidden = true;
    $("#suggestions").hidden = true;
    const data = await api("/api/cart", undefined, "GET");
    bubble(t("cartNotice"));
    if (!data.items.length) bubble(t("empty"));
    for (const item of data.items) product(item.product, null, item.quantity);
  } else {
    const history = await api("/api/history", undefined, "GET");
    messages.replaceChildren();
    if (history.messages.length) {
      for (const message of history.messages) bubble(message.text, message.role);
      bubble(t("restored"), "meta");
    } else bubble(t("greeting"));
  }
});
