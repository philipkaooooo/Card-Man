// Private: your Mac reads pending deletions here and confirms them once purged.
import { deletionsHandler } from "../../lib/core.js";

export const config = { api: { bodyParser: false } };
export default deletionsHandler();
