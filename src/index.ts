import './env.js';
import { resolveDbPath } from './env.js';
import { startServer } from './mcp/server.js';

const dbPath = resolveDbPath(process.argv.slice(2));

if (!dbPath) {
  console.error('Usage: node dist/index.js --db <path-to-eba.db>');
  process.exit(1);
}

startServer(dbPath).catch((error) => {
  console.error('Server error:', error);
  process.exit(1);
});
