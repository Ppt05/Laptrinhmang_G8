#pragma once
#include <array>
#include <cctype>
#include <iostream>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

struct Piece {
    std::string id, side, type;
    int x = 0, y = 0;
};

struct Move {
    std::string piece_id, from, to;
    std::optional<std::string> capture;
};

struct BoardState {
    int protocol_version = 2, move_number = 1, board_size = 9;
    std::string side;
    std::vector<Piece> pieces;
    std::vector<Move> legal_moves;
    // Indexed as board[y][x]. Empty squares contain std::nullopt.
    std::array<std::array<std::optional<Piece>, 9>, 9> board{};

    const Piece* at(int x, int y) const {
        if (x < 0 || x >= 9 || y < 0 || y >= 9 || !board[y][x]) return nullptr;
        return &*board[y][x];
    }
};

namespace ottv2_detail {
// A parser for protocol v2's JSON types: objects, arrays, strings, integers, null.
struct Json {
    enum Kind { Null, Number, String, Array, Object } kind = Null;
    int number = 0;
    std::string text;
    std::vector<Json> items;
    std::map<std::string, Json> fields;
    const Json& at(const std::string& key) const {
        if (kind != Object) throw std::runtime_error("Expected JSON object");
        return fields.at(key);
    }
    const std::string& str() const {
        if (kind != String) throw std::runtime_error("Expected JSON string");
        return text;
    }
    int integer() const {
        if (kind != Number) throw std::runtime_error("Expected JSON integer");
        return number;
    }
    const std::vector<Json>& list() const {
        if (kind != Array) throw std::runtime_error("Expected JSON array");
        return items;
    }
};

class Parser {
    const std::string& input;
    std::size_t pos = 0;
    [[noreturn]] void fail() const { throw std::runtime_error("Invalid protocol JSON"); }
    void space() {
        while (pos < input.size() && (input[pos] == ' ' || input[pos] == '\t' ||
               input[pos] == '\r' || input[pos] == '\n')) ++pos;
    }
    bool take(char c) {
        space();
        if (pos == input.size() || input[pos] != c) return false;
        ++pos;
        return true;
    }
    void expect(char c) { if (!take(c)) fail(); }
    unsigned hex4() {
        unsigned result = 0;
        for (int i = 0; i < 4; ++i) {
            if (pos == input.size()) fail();
            char c = input[pos++];
            unsigned digit;
            if (c >= '0' && c <= '9') digit = c - '0';
            else if (c >= 'a' && c <= 'f') digit = c - 'a' + 10;
            else if (c >= 'A' && c <= 'F') digit = c - 'A' + 10;
            else fail();
            result = result * 16 + digit;
        }
        return result;
    }
    static void utf8(std::string& result, unsigned code) {
        if (code <= 0x7f) result += char(code);
        else if (code <= 0x7ff) {
            result += char(0xc0 | (code >> 6)); result += char(0x80 | (code & 63));
        } else if (code <= 0xffff) {
            result += char(0xe0 | (code >> 12)); result += char(0x80 | ((code >> 6) & 63));
            result += char(0x80 | (code & 63));
        } else {
            result += char(0xf0 | (code >> 18)); result += char(0x80 | ((code >> 12) & 63));
            result += char(0x80 | ((code >> 6) & 63)); result += char(0x80 | (code & 63));
        }
    }
    std::string string() {
        expect('"');
        std::string result;
        while (pos < input.size()) {
            unsigned char c = input[pos++];
            if (c == '"') return result;
            if (c < 32) fail();
            if (c != '\\') { result += char(c); continue; }
            if (pos == input.size()) fail();
            c = input[pos++];
            if (c == '"' || c == '\\' || c == '/') result += char(c);
            else if (c == 'b') result += '\b';
            else if (c == 'f') result += '\f';
            else if (c == 'n') result += '\n';
            else if (c == 'r') result += '\r';
            else if (c == 't') result += '\t';
            else if (c == 'u') {
                unsigned code = hex4();
                if (code >= 0xd800 && code <= 0xdbff) {
                    if (pos + 2 > input.size() || input[pos++] != '\\' || input[pos++] != 'u') fail();
                    unsigned low = hex4();
                    if (low < 0xdc00 || low > 0xdfff) fail();
                    code = 0x10000 + ((code - 0xd800) << 10) + low - 0xdc00;
                } else if (code >= 0xdc00 && code <= 0xdfff) fail();
                utf8(result, code);
            } else fail();
        }
        fail();
    }
    Json value(int depth) {
        if (depth > 32) fail();
        space();
        if (pos == input.size()) fail();
        Json result;
        if (input[pos] == '{') {
            ++pos; result.kind = Json::Object;
            if (take('}')) return result;
            do {
                auto key = string(); expect(':');
                if (!result.fields.emplace(key, value(depth + 1)).second) fail();
                if (take('}')) return result;
            } while (take(','));
            fail();
        }
        if (input[pos] == '[') {
            ++pos; result.kind = Json::Array;
            if (take(']')) return result;
            do {
                result.items.push_back(value(depth + 1));
                if (take(']')) return result;
            } while (take(','));
            fail();
        }
        if (input[pos] == '"') { result.kind = Json::String; result.text = string(); return result; }
        if (input.compare(pos, 4, "null") == 0) { pos += 4; return result; }
        auto start = pos;
        if (input[pos] == '-') ++pos;
        auto digits = pos;
        while (pos < input.size() && input[pos] >= '0' && input[pos] <= '9') ++pos;
        if (pos == digits || (pos > digits + 1 && input[digits] == '0')) fail();
        result.kind = Json::Number;
        result.number = std::stoi(input.substr(start, pos - start));
        return result;
    }
public:
    explicit Parser(const std::string& line) : input(line) {}
    Json parse() {
        auto result = value(0); space();
        if (pos != input.size()) fail();
        return result;
    }
};

inline BoardState readState(const std::string& line) {
    auto json = Parser(line).parse();
    BoardState state;
    state.protocol_version = json.at("protocol_version").integer();
    state.move_number = json.at("move_number").integer();
    state.board_size = json.at("board_size").integer();
    state.side = json.at("side").str();
    if (state.protocol_version != 2 || state.board_size != 9 || state.move_number < 1 ||
        (state.side != "south" && state.side != "north")) throw std::runtime_error("Unsupported board state");
    for (const auto& item : json.at("pieces").list()) {
        Piece piece{item.at("id").str(), item.at("side").str(), item.at("type").str(),
                    item.at("x").integer(), item.at("y").integer()};
        if (piece.x < 0 || piece.x >= 9 || piece.y < 0 || piece.y >= 9 || state.board[piece.y][piece.x])
            throw std::runtime_error("Invalid piece position");
        state.board[piece.y][piece.x] = piece;
        state.pieces.push_back(piece);
    }
    for (const auto& item : json.at("legal_moves").list()) {
        Move move{item.at("piece_id").str(), item.at("from").str(), item.at("to").str(), std::nullopt};
        if (item.at("capture").kind != Json::Null) move.capture = item.at("capture").str();
        state.legal_moves.push_back(move);
    }
    return state;
}

inline std::string quote(const std::string& value) {
    std::string result = "\"";
    const char* hex = "0123456789abcdef";
    for (unsigned char c : value) {
        if (c == '"' || c == '\\') { result += '\\'; result += char(c); }
        else if (c < 32) { result += "\\u00"; result += hex[c >> 4]; result += hex[c & 15]; }
        else result += char(c);
    }
    return result + '"';
}
} // namespace ottv2_detail
