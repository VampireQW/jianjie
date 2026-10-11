// C ABI for the disk-backed filename index. Each call owns its SQLite connections.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "sqlite3.h"
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <set>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

using Cancel = int (*)();
using Progress = void (*)(long long);
using Row = void (*)(const char*, int, const char*);
#define JJ_API extern "C" __declspec(dllexport)

namespace {
constexpr const char* VERSION = "1";
struct Context {
    Cancel cancel;
    mutable ULONGLONG next_check = 0;
    mutable bool cancelled = false;
    bool stopped() const {
        auto now = GetTickCount64();
        if (cancel && now >= next_check) { cancelled = cancel() != 0; next_check = now + 10; }
        return cancelled;
    }
    void check() const { if (stopped()) throw std::runtime_error("cancelled"); }
    static int progress(void* p) { return static_cast<Context*>(p)->stopped(); }
    static int busy(void* p, int attempt) {
        auto* ctx = static_cast<Context*>(p);
        if (ctx->stopped() || attempt >= 600) return 0;
        Sleep(50);
        return !ctx->stopped();
    }
};
struct Db {
    sqlite3* p = nullptr;
    Db(const char* path, bool write, Context& ctx, bool create = false) {
        int flags = write ? SQLITE_OPEN_READWRITE : SQLITE_OPEN_READONLY;
        if (create) flags |= SQLITE_OPEN_CREATE;
        int code = sqlite3_open_v2(path, &p, flags | SQLITE_OPEN_NOMUTEX | SQLITE_OPEN_URI, nullptr);
        if (code != SQLITE_OK) {
            std::string error = p ? sqlite3_errmsg(p) : "SQLite allocation failed";
            sqlite3_close(p); p = nullptr;
            throw std::runtime_error(error);
        }
        sqlite3_busy_handler(p, Context::busy, &ctx);
        sqlite3_progress_handler(p, 1000, Context::progress, &ctx);
        try { exec("PRAGMA cache_size=-2048; PRAGMA temp_store=FILE; PRAGMA mmap_size=0"); }
        catch (...) { sqlite3_close(p); p = nullptr; throw; }
    }
    ~Db() { if (p) sqlite3_close(p); }
    void exec(const char* sql) {
        char* error = nullptr;
        int code = sqlite3_exec(p, sql, nullptr, nullptr, &error);
        if (code != SQLITE_OK) {
            std::string message = error ? error : sqlite3_errmsg(p);
            sqlite3_free(error);
            throw std::runtime_error(message);
        }
    }
    Db(const Db&) = delete;
    Db& operator=(const Db&) = delete;
};
struct Statement {
    sqlite3_stmt* p = nullptr;
    explicit Statement(Db& db, const std::string& sql) {
        if (sqlite3_prepare_v2(db.p, sql.c_str(), -1, &p, nullptr) != SQLITE_OK)
            throw std::runtime_error(sqlite3_errmsg(db.p));
    }
    ~Statement() { sqlite3_finalize(p); }
    void text(int index, const std::string& value) {
        if (sqlite3_bind_text(p, index, value.c_str(), int(value.size()), SQLITE_TRANSIENT) != SQLITE_OK)
            throw std::runtime_error("SQLite text binding failed");
    }
    void integer(int index, long long value) { sqlite3_bind_int64(p, index, value); }
    bool step() {
        int code = sqlite3_step(p);
        if (code == SQLITE_ROW) return true;
        if (code == SQLITE_DONE) return false;
        throw std::runtime_error(sqlite3_errmsg(sqlite3_db_handle(p)));
    }
    void reset() { sqlite3_reset(p); sqlite3_clear_bindings(p); }
    long long number(int col) { return sqlite3_column_int64(p, col); }
    std::string str(int col) {
        auto* value = sqlite3_column_text(p, col);
        return value ? std::string(reinterpret_cast<const char*>(value), sqlite3_column_bytes(p, col)) : "";
    }
};
struct Transaction {
    Db& db;
    bool done = false;
    Transaction(Db& value, bool writer = false) : db(value) { db.exec(writer ? "BEGIN IMMEDIATE" : "BEGIN"); }
    ~Transaction() { if (!done) sqlite3_exec(db.p, "ROLLBACK", nullptr, nullptr, nullptr); }
    void commit() { db.exec("COMMIT"); done = true; }
};
struct WriterLock {
    HANDLE handle;
    bool owned = false;
    WriterLock(const char* path, Context& ctx, bool wait) {
        unsigned long long hash = 14695981039346656037ULL;
        for (const unsigned char* s = reinterpret_cast<const unsigned char*>(path); *s; ++s) {
            unsigned char ch = *s == '\\' ? '/' : *s;
            if (ch >= 'A' && ch <= 'Z') ch += 'a' - 'A';
            hash = (hash ^ ch) * 1099511628211ULL;
        }
        wchar_t name[80];
        swprintf(name, 80, L"Local\\JianJieSearchNative_%016llx", hash);
        handle = CreateMutexW(nullptr, FALSE, name);
        if (!handle) throw std::runtime_error("Cannot create search index mutex");
        do {
            DWORD result = WaitForSingleObject(handle, wait ? 200 : 0);
            if (result == WAIT_OBJECT_0 || result == WAIT_ABANDONED) { owned = true; break; }
            if (result != WAIT_TIMEOUT || ctx.stopped()) break;
        } while (wait);
    }
    ~WriterLock() { if (owned) ReleaseMutex(handle); CloseHandle(handle); }
};
void error_text(char* target, int cap, const std::exception& error) {
    if (target && cap > 0) { std::snprintf(target, size_t(cap), "%s", error.what()); }
}
std::string meta(Db& db, const std::string& key) {
    Statement statement(db, "SELECT v FROM meta WHERE k=?");
    statement.text(1, key);
    return statement.step() ? statement.str(0) : "";
}
bool ready(Db& source, Db& fast) {
    try {
        auto id = meta(source, "native_source_id");
        return !id.empty() && meta(fast, "version") == VERSION && meta(fast, "source_id") == id;
    } catch (...) { return false; }
}
std::vector<unsigned> decode(const std::string& text) {
    std::vector<unsigned> result;
    for (size_t i = 0; i < text.size();) {
        unsigned c = static_cast<unsigned char>(text[i++]);
        unsigned value = c; int continuation = 0;
        if ((c & 0xe0) == 0xc0) { value = c & 0x1f; continuation = 1; }
        else if ((c & 0xf0) == 0xe0) { value = c & 0x0f; continuation = 2; }
        else if ((c & 0xf8) == 0xf0) { value = c & 7; continuation = 3; }
        for (int j = 0; j < continuation && i < text.size(); ++j)
            value = (value << 6) | (static_cast<unsigned char>(text[i++]) & 0x3f);
        result.push_back(value);
    }
    return result;
}
std::set<std::string> keys(const std::string& text, bool index) {
    auto chars = decode(text);
    std::set<std::string> result;
    char token[24];
    for (size_t i = 0; i < chars.size(); ++i) {
        if (index || chars.size() == 1) {
            std::snprintf(token, sizeof(token), "u%08x", chars[i]); result.insert(token);
        }
        if (i + 1 < chars.size()) {
            std::snprintf(token, sizeof(token), "b%08x%08x", chars[i], chars[i+1]); result.insert(token);
        }
    }
    return result;
}
std::string grams(const std::string& text) {
    std::string result;
    for (auto& key : keys(text, true)) { if (!result.empty()) result += ' '; result += key; }
    return result;
}
void gram_function(sqlite3_context* ctx, int, sqlite3_value** argv) {
    try {
        const auto* text = sqlite3_value_text(argv[0]);
        auto value = grams(text ? reinterpret_cast<const char*>(text) : "");
        sqlite3_result_text(ctx, value.c_str(), int(value.size()), SQLITE_TRANSIENT);
    } catch (const std::exception& error) { sqlite3_result_error(ctx, error.what(), -1); }
}
void writer(Db& fast) {
    fast.exec("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA cache_size=-4096; PRAGMA wal_autocheckpoint=1000");
    if (sqlite3_create_function_v2(fast.p, "jj_grams", 1, SQLITE_UTF8 | SQLITE_DETERMINISTIC,
                                 nullptr, gram_function, nullptr, nullptr, nullptr) != SQLITE_OK)
        throw std::runtime_error(sqlite3_errmsg(fast.p));
}
void schema(Db& fast) {
    fast.exec(
        "DROP TABLE IF EXISTS grams; DROP TABLE IF EXISTS names; DROP TABLE IF EXISTS meta;"
        "CREATE TABLE meta(k TEXT PRIMARY KEY,v TEXT);"
        "CREATE TABLE names(fid INTEGER PRIMARY KEY,lname TEXT NOT NULL);"
        "CREATE VIRTUAL TABLE grams USING fts5(lname,content='names',content_rowid='fid',detail=none,columnsize=0,tokenize='ascii');"
        "CREATE TRIGGER names_ai AFTER INSERT ON names BEGIN INSERT INTO grams(rowid,lname) VALUES(new.fid,jj_grams(new.lname)); END;"
        "CREATE TRIGGER names_ad AFTER DELETE ON names BEGIN INSERT INTO grams(grams,rowid,lname) VALUES('delete',old.fid,jj_grams(old.lname)); END;"
        "CREATE TRIGGER names_au AFTER UPDATE ON names BEGIN "
        "INSERT INTO grams(grams,rowid,lname) VALUES('delete',old.fid,jj_grams(old.lname));"
        "INSERT INTO grams(rowid,lname) VALUES(new.fid,jj_grams(new.lname)); END;"
    );
}
long long sync(Db& source, Db& fast, Context& ctx, int limit) {
    long long total = 0;
    while (total < limit) {
        ctx.check();
        Transaction input(source, true);
        std::vector<long long> ids;
        long long maximum = 0;
        {
            Statement changes(source, "SELECT seq,fid FROM jj_search_dirty_v1 ORDER BY seq LIMIT ?");
            changes.integer(1, std::min(512LL, limit - total));
            while (changes.step()) { maximum = changes.number(0); ids.push_back(changes.number(1)); }
        }
        if (ids.empty()) { input.commit(); break; }
        total += ids.size();
        std::sort(ids.begin(), ids.end()); ids.erase(std::unique(ids.begin(), ids.end()), ids.end());
        Transaction output(fast, true);
        {
            Statement lookup(source, "SELECT lname FROM files WHERE rowid=?");
            Statement add(fast, "INSERT INTO names(fid,lname) VALUES(?,?) ON CONFLICT(fid) DO UPDATE SET lname=excluded.lname WHERE lname<>excluded.lname");
            Statement remove(fast, "DELETE FROM names WHERE fid=?");
            for (auto id : ids) {
                ctx.check(); lookup.integer(1, id);
                if (lookup.step()) { add.integer(1, id); add.text(2, lookup.str(0)); add.step(); add.reset(); }
                else { remove.integer(1, id); remove.step(); remove.reset(); }
                lookup.reset();
            }
        }
        // Commit postings before acknowledging the source queue; replay is idempotent after a crash.
        output.commit();
        Statement acknowledge(source, "DELETE FROM jj_search_dirty_v1 WHERE seq<=?");
        acknowledge.integer(1, maximum); acknowledge.step();
        input.commit();
    }
    return total;
}
std::string resolve(Statement& parent, const std::string& volume, long long frn, Context& ctx,
                    std::unordered_map<std::string,std::string>& memo, size_t& bytes) {
    std::vector<std::pair<std::string,std::string>> visited;
    std::vector<std::string> parts;
    long long current = frn;
    while (current && current != 5 && visited.size() < 64) {
        ctx.check();
        auto key = volume + ':' + std::to_string(current);
        auto found = memo.find(key);
        if (found != memo.end()) { parts.push_back(found->second); break; }
        parent.text(1, volume); parent.integer(2, current);
        if (!parent.step()) { parent.reset(); break; }
        current = parent.number(0);
        auto name = parent.str(1); parent.reset();
        parts.push_back(name); visited.emplace_back(key, name);
    }
    std::string suffix;
    for (size_t i = parts.size(); i-- > 0;) {
        if (!suffix.empty()) suffix += '\\'; suffix += parts[i];
        if (i < visited.size() && memo.size() < 4096 && bytes + suffix.size() + visited[i].first.size() < 1024*1024) {
            bytes += suffix.size() + visited[i].first.size(); memo[visited[i].first] = suffix;
        }
    }
    return volume + '\\' + suffix;
}
std::string readonly_uri(const char* path) {
    std::string out = "file:/";
    char encoded[4];
    for (auto* p = reinterpret_cast<const unsigned char*>(path); *p; ++p) {
        if (*p == '\\') out += '/';
        else if (*p == '%' || *p == '?' || *p == '#') { std::snprintf(encoded, sizeof(encoded), "%%%02X", *p); out += encoded; }
        else out += char(*p);
    }
    return out + "?mode=ro";
}
} // namespace

JJ_API int jj_version() { return 1; }
JJ_API int jj_ready(const char* source_path, const char* fast_path) {
    Context ctx{nullptr};
    try { Db source(source_path, false, ctx); Db fast(fast_path, false, ctx); return ready(source, fast); }
    catch (...) { return 0; }
}
JJ_API int jj_build(const char* source_path, const char* fast_path, int force, Cancel cancelled,
                    Progress progress, char* error, int cap) {
    Context ctx{cancelled};
    try {
        WriterLock lock(source_path, ctx, true); ctx.check();
        if (!lock.owned) throw std::runtime_error("Cannot acquire search index mutex");
        Db source(source_path, true, ctx); Db fast(fast_path, true, ctx, true); writer(fast);
        bool rebuild = force || !ready(source, fast);
        if (!rebuild) {
            Statement dirty(source, "SELECT count(*) FROM (SELECT seq FROM jj_search_dirty_v1 LIMIT 20001)");
            rebuild = dirty.step() && dirty.number(0) > 20000;
        }
        if (rebuild) {
            long long watermark = 0, count = 0;
            {
                Transaction snapshot(source);
                { Statement seq(source, "SELECT coalesce(max(seq),0) FROM jj_search_dirty_v1"); if (seq.step()) watermark = seq.number(0); }
                const auto source_id = meta(source, "native_source_id");
                if (source_id.empty()) throw std::runtime_error("Missing source index identity");
                Transaction output(fast, true); schema(fast);
                {
                    Statement rows(source, "SELECT rowid,lname FROM files");
                    Statement insert(fast, "INSERT INTO names(fid,lname) VALUES(?,?)");
                    while (rows.step()) {
                        ctx.check(); insert.integer(1, rows.number(0)); insert.text(2, rows.str(1)); insert.step(); insert.reset();
                        if (++count % 4096 == 0 && progress) progress(count);
                    }
                    Statement state(fast, "INSERT INTO meta(k,v) VALUES('version',?),('source_id',?)");
                    state.text(1, VERSION); state.text(2, source_id); state.step();
                }
                output.commit(); snapshot.commit();
            }
            // Retain changes newer than the source snapshot, including repeated changes to the same rowid.
            while (watermark > 0) {
                ctx.check(); Transaction transaction(source, true);
                Statement remove(source, "DELETE FROM jj_search_dirty_v1 WHERE seq IN (SELECT seq FROM jj_search_dirty_v1 WHERE seq<=? ORDER BY seq LIMIT 4096)");
                remove.integer(1, watermark); remove.step();
                int changed = sqlite3_changes(source.p); transaction.commit();
                if (!changed) break;
            }
            if (progress) progress(count);
        }
        while (sync(source, fast, ctx, 8192) > 0) ctx.check();
        // Passive checkpoint never waits for an active search reader.
        sqlite3_wal_checkpoint_v2(fast.p, nullptr, SQLITE_CHECKPOINT_PASSIVE, nullptr, nullptr);
        return 0;
    } catch (const std::exception& e) { error_text(error, cap, e); return ctx.stopped() ? 2 : -1; }
}
JJ_API int jj_sync(const char* source_path, const char* fast_path, int limit, Cancel cancelled, char* error, int cap) {
    Context ctx{cancelled};
    try {
        WriterLock lock(source_path, ctx, false);
        if (!lock.owned || ctx.stopped()) return 0;
        Db source(source_path, true, ctx); Db fast(fast_path, true, ctx);
        if (!ready(source, fast)) return 0;
        writer(fast);
        return int(sync(source, fast, ctx, std::max(1, limit)));
    } catch (const std::exception& e) { error_text(error, cap, e); return ctx.stopped() ? 0 : -1; }
}
JJ_API int jj_query(const char* source_path, const char* fast_path, const char** terms, int term_count,
                    int limit, Cancel cancelled, Row row_callback, char* error, int cap) {
    Context ctx{cancelled};
    try {
        Db source(source_path, false, ctx);
        bool indexed = false;
        try { Db fast(fast_path, false, ctx); indexed = ready(source, fast); } catch (...) {}
        std::string sql = "SELECT f.name,f.isdir,f.path,f.v,f.frn FROM files f WHERE ";
        std::string match;
        if (indexed) {
            Statement attach(source, "ATTACH DATABASE ? AS fast"); attach.text(1, readonly_uri(fast_path)); attach.step();
            source.exec("PRAGMA fast.cache_size=-2048; PRAGMA fast.mmap_size=0");
            std::set<std::string> tokens;
            for (int i = 0; i < term_count; ++i) { auto next = keys(terms[i], false); tokens.insert(next.begin(), next.end()); }
            for (auto& token : tokens) { if (!match.empty()) match += " AND "; match += token; }
            sql = "SELECT f.name,f.isdir,f.path,f.v,f.frn FROM fast.grams JOIN files f ON f.rowid=grams.rowid WHERE grams MATCH ? AND ";
        }
        for (int i = 0; i < term_count; ++i) { if (i) sql += " AND "; sql += "instr(f.lname,?)>0"; }
        sql += " LIMIT ?";
        Statement rows(source, sql); int binding = 1;
        if (indexed) rows.text(binding++, match);
        for (int i = 0; i < term_count; ++i) rows.text(binding++, terms[i]);
        rows.integer(binding, limit + 1);
        Statement parent(source, "SELECT parent,name FROM files WHERE v=? AND frn=? AND frn>0 LIMIT 1");
        std::unordered_map<std::string,std::string> memo; size_t memo_bytes = 0; int count = 0;
        while (rows.step()) {
            ctx.check(); if (count++ >= limit) return 1;
            auto name = rows.str(0), path = rows.str(2);
            if (path.empty()) path = resolve(parent, rows.str(3), rows.number(4), ctx, memo, memo_bytes);
            if (row_callback) row_callback(name.c_str(), int(rows.number(1)), path.c_str());
        }
        ctx.check(); return 0;
    } catch (const std::exception& e) { error_text(error, cap, e); return ctx.stopped() ? 2 : -1; }
}
