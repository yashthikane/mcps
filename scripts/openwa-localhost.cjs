// Preloaded into OpenWA with `node --require` (see scripts/openwa.ps1). OpenWA calls app.listen(port),
// which listens on every network interface; Donna keeps every service on 127.0.0.1. Servers that
// don't name a host are bound to loopback, without changing OpenWA itself.
const net = require("net");

const listen = net.Server.prototype.listen;
net.Server.prototype.listen = function (...args) {
  const first = args[0];
  if (typeof first === "number" || (typeof first === "string" && /^\d+$/.test(first))) {
    if (typeof args[1] !== "string") args.splice(1, 0, "127.0.0.1");
  } else if (first && typeof first === "object" && first.path === undefined && !first.host) {
    args[0] = { ...first, host: "127.0.0.1" };
  }
  return listen.apply(this, args);
};
