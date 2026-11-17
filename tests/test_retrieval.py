from app.services.retrieval import cosine_similarity, lexical_similarity


def test_cosine_similarity_identical_vectors():
    assert cosine_similarity([1, 0, 1], [1, 0, 1]) > 0.99


def test_cosine_similarity_orthogonal_vectors():
    assert abs(cosine_similarity([1, 0], [0, 1])) < 0.0001


def test_lexical_similarity_rewards_shared_terms():
    related = lexical_similarity("redes neurais e aprendizado profundo", "redes neurais aprendem padrões")
    unrelated = lexical_similarity("redes neurais e aprendizado profundo", "receita de bolo de chocolate")
    assert related > unrelated
