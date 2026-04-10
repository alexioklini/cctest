
const { McpServer } = require('@modelcontextprotocol/sdk');
const Database = require('better-sqlite3');
const path = require('path');
const fs = require('fs');

class AgentDbServer extends McpServer {
    constructor(dbDirectory) {
        super();
        this.dbDirectory = dbDirectory;
        this.databases = {}; // Store opened database connections

        // Discover and open all .db files in the directory
        this._discoverDatabases();

        // Register the tool using the McpServer.tool method
        this.tool('query_db', 'Executes a read-only SQL query against a specified internal database.', {
            db_name: {
                type: 'string',
                description: 'The name of the database to query (e.g., "costs", "audit", "memory", "scheduler", "chats", "context", "traces", "Reporter", "Researcher").',
                enum: [], // Will be populated dynamically
            },
            sql: {
                type: 'string',
                description: 'The SQL query to execute (SELECT statements only).',
            },
        }, this.queryDb.bind(this)); // Bind 'this' to the method
    }

    _discoverDatabases() {
        if (!fs.existsSync(this.dbDirectory)) {
            console.error(`Database directory not found: ${this.dbDirectory}`);
            return;
        }

        const files = fs.readdirSync(this.dbDirectory);
        // Look in the dbDirectory itself and in subdirectories named after agents
        const possibleDbDirs = [this.dbDirectory];
        const agentDirs = fs.readdirSync(this.dbDirectory, { withFileTypes: true })
            .filter(dirent => dirent.isDirectory())
            .map(dirent => path.join(this.dbDirectory, dirent.name));
        possibleDbDirs.push(...agentDirs);


        for (const dir of possibleDbDirs) {
            if (!fs.existsSync(dir)) continue;

            const dbFiles = fs.readdirSync(dir);
            for (const file of dbFiles) {
                if (file.endsWith('.db')) {
                    const dbPath = path.join(dir, file);
                    const dbName = path.basename(file, '.db');
                    if (!this.databases[dbName]) { // Only open each DB once
                        try {
                            this.databases[dbName] = new Database(dbPath, { readonly: true });
                            console.log(`Opened database: ${dbName} at ${dbPath}`);
                        } catch (error) {
                            console.error(`Error opening database ${dbName} at ${dbPath}: ${error.message}`);
                        }
                    }
                }
            }
        }
    }

    async queryDb(db_name, sql) {
        if (!this.databases[db_name]) {
            throw new Error(`Database "${db_name}" not found or not loaded.`);
        }

        if (!sql.trim().toLowerCase().startsWith('select')) {
            throw new Error('Only SELECT queries are allowed for security reasons.');
        }

        try {
            const stmt = this.databases[db_name].prepare(sql);
            const rows = stmt.all();
            return JSON.stringify(rows, null, 2);
        } catch (error) {
            throw new Error(`SQL query failed: ${error.message}`);
        }
    }

    // Override the getTools method to dynamically set the enum for db_name
    getTools() {
        const tools = super.getTools();
        const queryDbTool = tools.find(t => t.name === 'query_db');
        if (queryDbTool) {
            queryDbTool.parameters.properties.db_name.enum = Object.keys(this.databases);
        }
        return tools;
    }
}

async function main() {
    // Expect the directory containing the agent's .db files as a command-line argument
    // This will typically be the AGENTS_DIR or a specific agent's directory
    const dbDirectory = process.argv[2]; 

    if (!dbDirectory) {
        console.error('Usage: node index.js <path_to_agent_db_directory>');
        process.exit(1);
    }

    const server = new AgentDbServer(dbDirectory);
    await server.start();

    // Ensure databases are closed on shutdown
    const shutdown = () => {
        console.log('Closing databases...');
        for (const dbName in server.databases) {
            try {
                server.databases[dbName].close();
            } catch (error) {
                console.error(`Error closing database ${dbName}: ${error.message}`);
            }
        }
        process.exit(0);
    };

    process.on('SIGINT', shutdown);
    process.on('SIGTERM', shutdown);
}

main().catch(console.error);
