// Cloud Run entry point: one small HTTP server for the three routes.
import http from "node:http";
import { deletionsHandler, notificationsHandler, send } from "./lib/core.js";

const notify = notificationsHandler();
const deletions = deletionsHandler();
const ROUTES = { "/api/ebay/notifications": notify, "/api/marketplace/notifications": notify, "/api/ebay/deletions": deletions };

export const server = http.createServer((req, res) => {
  const path = new URL(req.url, "https://internal").pathname.replace(/\/+$/, "");
  const h = ROUTES[path];
  if (!h) return send(res, 404, { error: "not found" });
  return h(req, res);
});
server.requestTimeout = 30_000;
if (process.env.NODE_ENV !== "test") server.listen(Number(process.env.PORT || 8080), () => console.log("listening"));
