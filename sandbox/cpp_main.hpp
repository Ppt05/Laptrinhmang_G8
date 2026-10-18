// Appended by the judge after the submitted chooseMove function.
int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    try {
        std::string line;
        while (std::getline(std::cin, line)) {
            const BoardState state = ottv2_detail::readState(line);
            const Move move = chooseMove(state);
            std::cout << "{\"piece_id\":" << ottv2_detail::quote(move.piece_id)
                      << ",\"to\":" << ottv2_detail::quote(move.to) << "}" << std::endl;
        }
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
