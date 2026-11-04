#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Лабораторная работа №2.
Синтаксический анализатор DSL запросов к базе данных «Автопарк».
Метод: рекурсивный спуск по формальной грамматике (без spaCy/Stanza/DeepPavlov).

Запуск:
    python query_parser.py queries.txt
    python query_parser.py queries.txt result.txt   # дополнительно записать вывод в файл

Грамматика:
    Query      : Action, [Quantifier], Object, [Source], [ConditionPart]
    Source     : "из", "таблицы", TableName
    ConditionPart : "если", OrExpr
    OrExpr     : AndExpr, {"или", AndExpr}
    AndExpr    : NotExpr, {"и", NotExpr}
    NotExpr    : ["не"], Term
    Term       : FieldTerm | AdjTerm
    FieldTerm  : [Subject, "имеет" | "имеет"], Field, [Operator], Value
    AdjTerm    : [Subject], ["не"], Adjective
    Operator   : ["не"], ("равен" | "равна" | "равно" | "больше" | "меньше")
"""

import re
import sys
from dataclasses import dataclass


# ---------------------------------------------------------------- словари
def norm(s: str) -> str:
    """Нижний регистр и ё -> е (чтобы «чёрный» и «черный» были одним словом)."""
    return s.lower().replace("ё", "е")


ACTIONS = {"выбрать", "показать", "найти", "удалить"}
QUANTIFIERS = {"все", "один"}
OBJECTS = {"машины", "автомобили", "авто", "автомобиль", "машину"}
SUBJECTS = {"машина", "автомобиль", "авто"}
FIELDS = {"цвет", "марка", "модель", "год", "пробег", "цена", "кузов",
          "коробка", "привод", "мощность", "владелец"}
NUMERIC = {"год", "пробег", "цена", "мощность"}
OPS = {"равен", "равна", "равно", "больше", "меньше"}
KEYWORDS = {"и", "или", "не", "если", "из", "таблицы", "имеет"}

# прилагательные -> (поле, значение)
_COLOR_FORMS = {
    "красный": ["красный", "красная", "красное", "красные"],
    "белый": ["белый", "белая", "белое", "белые"],
    "чёрный": ["чёрный", "чёрная", "чёрное", "чёрные"],
    "синий": ["синий", "синяя", "синее", "синие"],
    "серый": ["серый", "серая", "серое", "серые"],
    "зелёный": ["зелёный", "зелёная", "зелёное", "зелёные"],
    "жёлтый": ["жёлтый", "жёлтая", "жёлтое", "жёлтые"],
    "коричневый": ["коричневый", "коричневая", "коричневое", "коричневые"],
    "бежевый": ["бежевый", "бежевая", "бежевое", "бежевые"],
    "серебристый": ["серебристый", "серебристая", "серебристое", "серебристые"],
}
ADJ = {norm(form): ("цвет", color)
       for color, forms in _COLOR_FORMS.items() for form in forms}


# ---------------------------------------------------------------- лексер
@dataclass
class Token:
    type: str   # NUMBER | STRING | WORD | EOF
    value: str
    pos: int


TOKEN_RE = re.compile(r'"[^"]*"|[^\s"]+')


def tokenize(text: str):
    tokens = []
    for m in TOKEN_RE.finditer(text):
        v = m.group()
        if v.startswith('"'):
            tokens.append(Token("STRING", v.strip('"'), m.start()))
        else:
            v = v.strip(".,!?;:")
            if v:
                tokens.append(Token("NUMBER" if v.isdigit() else "WORD", v, m.start()))
    tokens.append(Token("EOF", "", len(text)))
    return tokens


# ---------------------------------------------------------------- дерево
class Node:
    def __init__(self, name, value="", children=None):
        self.name = name
        self.value = value
        self.children = children or []

    def render(self, prefix="", is_last=True, is_root=True):
        label = self.name + (f": {self.value}" if self.value else "")
        if is_root:
            lines = [label]
            child_prefix = ""
        else:
            lines = [prefix + ("└── " if is_last else "├── ") + label]
            child_prefix = prefix + ("    " if is_last else "│   ")
        for i, ch in enumerate(self.children):
            lines += ch.render(child_prefix, i == len(self.children) - 1, False)
        return lines

    def __str__(self):
        return "\n".join(self.render())


# ---------------------------------------------------------------- парсер
class ParseError(Exception):
    def __init__(self, msg, tok):
        super().__init__(f"{msg} (позиция {tok.pos})")
        self.tok = tok


class Parser:
    def __init__(self, tokens):
        self.t = tokens
        self.i = 0

    # --- вспомогательные
    def peek(self):
        return self.t[self.i]

    def low(self):
        tok = self.peek()
        return None if tok.type == "STRING" else norm(tok.value)

    def next_low(self):
        tok = self.t[min(self.i + 1, len(self.t) - 1)]
        return None if tok.type == "STRING" else norm(tok.value)

    def accept(self, *vals):
        cur = self.low()
        if cur is not None and cur in vals:
            tok = self.peek()
            self.i += 1
            return tok
        return None

    def expect(self, vals, what):
        tok = self.accept(*vals)
        if tok is None:
            cur = self.peek()
            found = cur.value if cur.type != "EOF" else "конец запроса"
            raise ParseError(f"ожидалось {what}, найдено '{found}'", cur)
        return tok

    # --- Query
    def parse(self):
        root = Node("Query")
        act = self.expect(ACTIONS, "действие (выбрать/показать/найти/удалить)")
        root.children.append(Node("Action", norm(act.value)))

        q = self.accept(*QUANTIFIERS)
        if q:
            root.children.append(Node("Quantifier", norm(q.value)))

        obj = self.expect(OBJECTS, "объект (машины/автомобили/авто/...)")
        root.children.append(Node("Object", norm(obj.value)))

        if self.accept("из"):
            self.expect({"таблицы"}, "'таблицы'")
            name = self.peek()
            if name.type == "EOF" or (name.type == "WORD" and norm(name.value) in KEYWORDS):
                raise ParseError("ожидалось имя таблицы", name)
            self.i += 1
            root.children.append(Node("Source", name.value))

        if self.accept("если"):
            root.children.append(Node("Condition", children=[self.or_expr()]))

        if self.peek().type != "EOF":
            raise ParseError(f"лишний токен '{self.peek().value}'", self.peek())
        return root

    # --- логика: ИЛИ -> И -> НЕ
    def or_expr(self):
        parts = [self.and_expr()]
        while self.accept("или"):
            parts.append(self.and_expr())
        return parts[0] if len(parts) == 1 else Node("OR", children=parts)

    def and_expr(self):
        parts = [self.not_expr()]
        while self.accept("и"):
            parts.append(self.not_expr())
        return parts[0] if len(parts) == 1 else Node("AND", children=parts)

    def not_expr(self):
        if self.accept("не"):
            if self.low() == "не":
                raise ParseError("двойное отрицание 'не не' не допускается", self.peek())
            return Node("NOT", children=[self.term()])
        return self.term()

    # --- Term
    def term(self):
        subj = self.accept(*SUBJECTS)

        # AdjTerm: [Subject] ["не"] Adjective
        if self.low() in ADJ or (self.low() == "не" and self.next_low() in ADJ):
            neg = self.accept("не")
            adj = self.accept(*ADJ)
            field, val = ADJ[norm(adj.value)]
            node = Node("Term", children=self._subj(subj) + [
                Node("Field", field), Node("Operator", "равен"), Node("Value", val)])
            return Node("NOT", children=[node]) if neg else node

        # FieldTerm
        if subj:
            self.expect({"имеет"}, "'имеет' или прилагательное (например, 'красная')")
            has = True
        else:
            has = bool(self.accept("имеет"))

        field = self.field()

        if has and self.low() not in OPS and self.low() != "не":
            op_text, op_tok = "равен (по умолчанию)", None
        else:
            op_text, base, op_tok = self.operator()
            if base in ("больше", "меньше") and field not in NUMERIC:
                raise ParseError(
                    f"оператор '{base}' неприменим к нечисловому полю '{field}'", op_tok)

        val_tok = self.value()
        if field in NUMERIC and val_tok.type != "NUMBER":
            raise ParseError(f"для поля '{field}' ожидалось число, найдено '{val_tok.value}'",
                             val_tok)

        return Node("Term", children=self._subj(subj) + [
            Node("Field", field), Node("Operator", op_text), Node("Value", val_tok.value)])

    @staticmethod
    def _subj(subj):
        return [Node("Subject", norm(subj.value))] if subj else []

    def field(self):
        if self.accept("номер"):
            self.expect({"двигателя"}, "'двигателя'")
            return "номер двигателя"
        return norm(self.expect(FIELDS, "поле (цвет/марка/год/...)").value)

    def operator(self):
        neg = self.accept("не")
        tok = self.expect(OPS, "оператор (равен/равна/равно/больше/меньше)")
        base = norm(tok.value)
        return ("не " if neg else "") + base, base, tok

    def value(self):
        tok = self.peek()
        if tok.type == "EOF":
            raise ParseError("ожидалось значение", tok)
        if tok.type == "WORD" and norm(tok.value) in KEYWORDS:
            raise ParseError(
                f"ожидалось значение, найдено служебное слово '{tok.value}' "
                f"(значение-фразу пишите в кавычках)", tok)
        self.i += 1
        return tok


# ---------------------------------------------------------------- запуск
def analyze(query: str):
    """Возвращает (успех, текст отчёта)."""
    lines = ["=" * 64, f"Запрос: {query}"]
    try:
        tree = Parser(tokenize(query)).parse()
        lines += ["Результат: УСПЕХ", "", "Дерево разбора:", str(tree)]
        return True, "\n".join(lines)
    except ParseError as e:
        lines += ["Результат: НЕУДАЧА", f"Ошибка: {e}"]
        pos = e.tok.pos
        lines += ["  " + query, "  " + " " * pos + "^"]
        return False, "\n".join(lines)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "queries.txt"
    out_path = sys.argv[2] if len(sys.argv) > 2 else None

    with open(path, encoding="utf-8") as f:
        queries = [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]

    report, ok = [], 0
    for q in queries:
        success, text = analyze(q)
        ok += success
        report.append(text)
    report.append("=" * 64)
    report.append(f"Итого: запросов {len(queries)}, успешно {ok}, с ошибками {len(queries) - ok}")

    result = "\n".join(report)
    print(result)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(result + "\n")


if __name__ == "__main__":
    main()
