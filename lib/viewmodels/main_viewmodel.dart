import 'package:flutter/material.dart';

/// メイン画面の状態管理を行うViewModel
class MainViewModel extends ChangeNotifier {
  final String _title = 'Crypto Radar 初期画面';
  String get title => _title;

  int _counter = 0;
  int get counter => _counter;

  /// カウンターをインクリメントする
  void incrementCounter() {
    _counter++;
    notifyListeners();
  }
}
