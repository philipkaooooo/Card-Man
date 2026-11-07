import { notificationsHandler } from "../../lib/core.js";

export const config = { api: { bodyParser: false } };   // the signature covers the raw bytes
export default notificationsHandler();
