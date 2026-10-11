const configuredMax = Number.parseInt(process.env.MAX_CONCURRENT_ROUTE_REQUESTS || '20', 10);
const maxActive = Number.isFinite(configuredMax) ? Math.max(1, configuredMax) : 20;
let active = 0;

function tryAcquireRouteSlot() {
  if (active >= maxActive) return null;
  active += 1;
  let released = false;
  return () => {
    if (released) return;
    released = true;
    active = Math.max(0, active - 1);
  };
}

module.exports = { tryAcquireRouteSlot };
