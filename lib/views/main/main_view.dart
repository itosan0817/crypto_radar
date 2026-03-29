import 'package:flutter/material.dart';
import '../../viewmodels/main_viewmodel.dart';

/// メイン画面のUIを描画するView
class MainView extends StatefulWidget {
  const MainView({super.key});

  @override
  State<MainView> createState() => _MainViewState();
}

class _MainViewState extends State<MainView> {
  final MainViewModel _viewModel = MainViewModel();

  @override
  void dispose() {
    _viewModel.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    // ListenableBuilderを使用して、ViewModelの変更を監視し画面を更新します
    return ListenableBuilder(
      listenable: _viewModel,
      builder: (context, child) {
        return Scaffold(
          appBar: AppBar(
            backgroundColor: Theme.of(context).colorScheme.inversePrimary,
            title: Text(_viewModel.title),
          ),
          body: Center(
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: <Widget>[
                const Text(
                  'ボタンが押された回数 (MVVM構成):',
                ),
                Text(
                  '${_viewModel.counter}',
                  style: Theme.of(context).textTheme.headlineMedium,
                ),
              ],
            ),
          ),
          floatingActionButton: FloatingActionButton(
            onPressed: _viewModel.incrementCounter,
            tooltip: '追加',
            child: const Icon(Icons.add),
          ),
        );
      },
    );
  }
}
