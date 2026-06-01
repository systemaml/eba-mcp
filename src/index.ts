import { startServer } from './mcp/server.js';

const args = process.argv.slice(2);
const dbIndex = args.indexOf('--db');
const dbPath = dbIndex >= 0 ? args[dbIndex + 1] : (process.env.EBA_DB_PATH || './data/eba.db');

if (!dbPath) {
  console.error('Usage: node dist/index.js --db <path-to-eba.db>');
  process.exit(1);
}

startServer(dbPath).catch((error) => {
  console.error('Server error:', error);
  process.exit(1);
});
